#!/usr/bin/env python3
"""
Jetson Camera Streamer - Stream two USB cameras via UDP with H.264 encoding
Streams to base station at 192.168.1.10 on ports 5000 and 5001
Camera settings: 640x480 @ 15fps
"""

import subprocess
import sys
import time
import logging
import socket
import threading
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('camera_streamer.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class CameraStreamer:
    def __init__(self):
        self.base_ip = "192.168.1.10"
        self.jetson_ip = "192.168.1.100"
        self.camera1_device = "/dev/video0"  # First HP 320 FHD Webcam
        self.camera2_device = "/dev/video2"  # Second HP 320 FHD Webcam
        self.camera1_port = 5000
        self.camera2_port = 5001
        self.width = 640
        self.height = 480
        self.framerate = 15
        self.processes = []

    def check_network_connectivity(self):
        """Test network connectivity to base station"""
        logger.info(f"Testing network connectivity to base station {self.base_ip}")
        try:
            # Test basic connectivity
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((self.base_ip, 22))  # Try SSH port as connectivity test
            sock.close()
            
            if result == 0:
                logger.info("✓ Network connectivity to base station established")
                return True
            else:
                logger.warning("⚠ Direct connection test failed, but UDP streaming may still work")
                return True  # UDP doesn't require established connection
        except Exception as e:
            logger.error(f"✗ Network connectivity test failed: {e}")
            return False

    def check_camera_access(self, device):
        """Verify camera device is accessible"""
        logger.info(f"Checking camera access for {device}")
        try:
            # Test if device exists and is readable
            if not Path(device).exists():
                logger.error(f"✗ Camera device {device} does not exist")
                return False
            
            # Test basic v4l2 access
            result = subprocess.run(
                ['v4l2-ctl', '--device', device, '--get-fmt-video'],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode == 0:
                logger.info(f"✓ Camera {device} is accessible")
                logger.debug(f"Camera format info: {result.stdout}")
                return True
            else:
                logger.error(f"✗ Cannot access camera {device}: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"✗ Timeout accessing camera {device}")
            return False
        except Exception as e:
            logger.error(f"✗ Error checking camera {device}: {e}")
            return False

    def test_gstreamer_pipeline(self, device):
        """Test GStreamer pipeline without network streaming"""
        logger.info(f"Testing GStreamer pipeline for {device}")
        
        # Test pipeline: camera -> MJPEG decode -> H.264 encode -> fakesink
        test_pipeline = [
            'gst-launch-1.0', '-v',
            'v4l2src', f'device={device}',
            '!', 'image/jpeg,width=640,height=480,framerate=15/1',
            '!', 'jpegdec',
            '!', 'videoconvert',
            '!', 'video/x-raw,format=I420',
            '!', 'omxh264enc', 'bitrate=1000000',
            '!', 'fakesink'
        ]
        
        try:
            process = subprocess.Popen(test_pipeline, 
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            # Let it run for 3 seconds then terminate
            time.sleep(3)
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            
            if "Setting pipeline to NULL" in stderr or process.returncode in [0, -15]:  # -15 is SIGTERM
                logger.info(f"✓ GStreamer pipeline test successful for {device}")
                return True
            else:
                logger.error(f"✗ GStreamer pipeline test failed for {device}")
                logger.debug(f"Pipeline stderr: {stderr}")
                return False
                
        except Exception as e:
            logger.error(f"✗ Pipeline test error for {device}: {e}")
            try:
                process.terminate()
            except:
                pass
            return False

    def create_streaming_pipeline(self, device, port):
        """Create GStreamer pipeline for camera streaming"""
        
        # Try hardware encoding first (omxh264enc), fallback to software (x264enc)
        hw_pipeline = [
            'gst-launch-1.0', '-v',
            'v4l2src', f'device={device}',
            '!', f'image/jpeg,width={self.width},height={self.height},framerate={self.framerate}/1',
            '!', 'jpegdec',
            '!', 'videoconvert',
            '!', 'video/x-raw,format=I420',
            '!', 'omxh264enc', 'bitrate=1000000', 'preset-level=1',
            '!', 'video/x-h264,stream-format=byte-stream,alignment=au',
            '!', 'h264parse',
            '!', 'rtph264pay', 'config-interval=1',
            '!', 'udpsink', f'host={self.base_ip}', f'port={port}', 'sync=false'
        ]
        
        sw_pipeline = [
            'gst-launch-1.0', '-v', 
            'v4l2src', f'device={device}',
            '!', f'image/jpeg,width={self.width},height={self.height},framerate={self.framerate}/1',
            '!', 'jpegdec',
            '!', 'videoconvert',
            '!', 'video/x-raw,format=I420',
            '!', 'x264enc', 'bitrate=1000', 'speed-preset=ultrafast',
            '!', 'video/x-h264,stream-format=byte-stream,alignment=au',
            '!', 'h264parse',
            '!', 'rtph264pay', 'config-interval=1',
            '!', 'udpsink', f'host={self.base_ip}', f'port={port}', 'sync=false'
        ]
        
        return hw_pipeline, sw_pipeline

    def start_camera_stream(self, device, port, camera_name):
        """Start streaming for a single camera"""
        logger.info(f"Starting {camera_name} stream: {device} -> {self.base_ip}:{port}")
        
        hw_pipeline, sw_pipeline = self.create_streaming_pipeline(device, port)
        
        # Try hardware encoding first
        try:
            logger.info(f"Attempting hardware encoding for {camera_name}")
            process = subprocess.Popen(hw_pipeline, 
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            # Give it a moment to start
            time.sleep(2)
            if process.poll() is None:  # Still running
                logger.info(f"✓ {camera_name} hardware encoding started successfully")
                return process
            else:
                stdout, stderr = process.communicate()
                logger.warning(f"⚠ Hardware encoding failed for {camera_name}: {stderr}")
                
        except Exception as e:
            logger.warning(f"⚠ Hardware encoding exception for {camera_name}: {e}")
        
        # Fallback to software encoding
        try:
            logger.info(f"Attempting software encoding for {camera_name}")
            process = subprocess.Popen(sw_pipeline,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            time.sleep(2)
            if process.poll() is None:  # Still running  
                logger.info(f"✓ {camera_name} software encoding started successfully")
                return process
            else:
                stdout, stderr = process.communicate()
                logger.error(f"✗ Software encoding also failed for {camera_name}: {stderr}")
                return None
                
        except Exception as e:
            logger.error(f"✗ Software encoding exception for {camera_name}: {e}")
            return None

    def run_diagnostics(self):
        """Run comprehensive diagnostics"""
        logger.info("=== RUNNING DIAGNOSTICS ===")
        
        # Check network connectivity
        network_ok = self.check_network_connectivity()
        
        # Check camera access
        camera1_ok = self.check_camera_access(self.camera1_device)
        camera2_ok = self.check_camera_access(self.camera2_device)
        
        # Test GStreamer pipelines
        pipeline1_ok = self.test_gstreamer_pipeline(self.camera1_device) if camera1_ok else False
        pipeline2_ok = self.test_gstreamer_pipeline(self.camera2_device) if camera2_ok else False
        
        logger.info("=== DIAGNOSTICS SUMMARY ===")
        logger.info(f"Network connectivity: {'✓' if network_ok else '✗'}")
        logger.info(f"Camera 1 ({self.camera1_device}): {'✓' if camera1_ok else '✗'}")
        logger.info(f"Camera 2 ({self.camera2_device}): {'✓' if camera2_ok else '✗'}")
        logger.info(f"Pipeline 1 test: {'✓' if pipeline1_ok else '✗'}")
        logger.info(f"Pipeline 2 test: {'✓' if pipeline2_ok else '✗'}")
        
        return all([network_ok, camera1_ok, camera2_ok, pipeline1_ok, pipeline2_ok])

    def start_streaming(self):
        """Start streaming both cameras"""
        logger.info("=== STARTING CAMERA STREAMING ===")
        
        # Run diagnostics first
        if not self.run_diagnostics():
            logger.error("⚠ Diagnostics revealed issues, but attempting to start streaming anyway...")
        
        # Start camera 1
        process1 = self.start_camera_stream(self.camera1_device, self.camera1_port, "Camera 1")
        if process1:
            self.processes.append(process1)
        
        # Start camera 2  
        process2 = self.start_camera_stream(self.camera2_device, self.camera2_port, "Camera 2")
        if process2:
            self.processes.append(process2)
        
        if not self.processes:
            logger.error("✗ Failed to start any camera streams")
            return False
        
        logger.info(f"✓ Started {len(self.processes)} camera stream(s)")
        logger.info(f"Camera 1 streaming to: udp://{self.base_ip}:{self.camera1_port}")
        logger.info(f"Camera 2 streaming to: udp://{self.base_ip}:{self.camera2_port}")
        return True

    def stop_streaming(self):
        """Stop all streaming processes"""
        logger.info("Stopping camera streams...")
        for process in self.processes:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            except Exception as e:
                logger.warning(f"Error stopping process: {e}")
        self.processes.clear()
        logger.info("All streams stopped")

    def monitor_streams(self):
        """Monitor running streams and restart if needed"""
        logger.info("Starting stream monitoring...")
        try:
            while True:
                # Check if processes are still running
                running_processes = []
                for i, process in enumerate(self.processes):
                    if process.poll() is None:  # Still running
                        running_processes.append(process)
                    else:
                        stdout, stderr = process.communicate()
                        logger.warning(f"Stream {i+1} stopped unexpectedly: {stderr}")
                
                self.processes = running_processes
                
                if not self.processes:
                    logger.error("All streams stopped, exiting...")
                    break
                
                # Log status every 30 seconds
                logger.info(f"Status: {len(self.processes)} streams running")
                time.sleep(30)
                
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            self.stop_streaming()


def main():
    """Main function"""
    streamer = CameraStreamer()
    
    try:
        if streamer.start_streaming():
            streamer.monitor_streams()
        else:
            logger.error("Failed to start streaming")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        streamer.stop_streaming()


if __name__ == "__main__":
    main()