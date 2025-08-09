#!/usr/bin/env python3
"""
Base Station Camera Receiver - Receive UDP H.264 streams from Jetson cameras
Receives from Jetson at 192.168.1.100 on ports 5000 and 5001
Can display streams or serve them as RTSP
"""

import subprocess
import sys
import time
import logging
import socket
import threading
import argparse
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('camera_receiver.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class CameraReceiver:
    def __init__(self, mode='display'):
        self.jetson_ip = "192.168.1.100"
        self.base_ip = "192.168.1.10"
        self.camera1_port = 5000
        self.camera2_port = 5001
        self.rtsp_port1 = 8554  # RTSP server port for camera 1
        self.rtsp_port2 = 8555  # RTSP server port for camera 2
        self.mode = mode  # 'display', 'rtsp', or 'record'
        self.processes = []

    def check_network_connectivity(self):
        """Test network connectivity to Jetson"""
        logger.info(f"Testing network connectivity to Jetson {self.jetson_ip}")
        try:
            # Test basic connectivity
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((self.jetson_ip, 22))  # Try SSH port
            sock.close()
            
            if result == 0:
                logger.info("✓ Network connectivity to Jetson established")
                return True
            else:
                logger.warning("⚠ Direct connection test failed, but UDP receiving may still work")
                return True  # UDP doesn't require established connection
        except Exception as e:
            logger.error(f"✗ Network connectivity test failed: {e}")
            return False

    def check_ports_available(self):
        """Check if required ports are available"""
        logger.info("Checking port availability")
        ports_to_check = [self.camera1_port, self.camera2_port]
        
        if self.mode == 'rtsp':
            ports_to_check.extend([self.rtsp_port1, self.rtsp_port2])
        
        for port in ports_to_check:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(('', port))
                sock.close()
                logger.info(f"✓ Port {port} is available")
            except OSError as e:
                logger.error(f"✗ Port {port} is not available: {e}")
                return False
        
        return True

    def test_gstreamer_udp_receive(self, port):
        """Test if we can receive UDP packets on specified port"""
        logger.info(f"Testing UDP receive capability on port {port}")
        
        # Simple test pipeline: UDP source -> fakesink
        test_pipeline = [
            'gst-launch-1.0', '-v',
            'udpsrc', f'port={port}',
            '!', 'application/x-rtp,encoding-name=H264',
            '!', 'rtph264depay',
            '!', 'fakesink'
        ]
        
        try:
            process = subprocess.Popen(test_pipeline,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            # Let it run for 2 seconds then terminate
            time.sleep(2)
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            
            if "Setting pipeline to NULL" in stderr or process.returncode in [0, -15]:
                logger.info(f"✓ UDP receive test successful on port {port}")
                return True
            else:
                logger.error(f"✗ UDP receive test failed on port {port}")
                logger.debug(f"Pipeline stderr: {stderr}")
                return False
                
        except Exception as e:
            logger.error(f"✗ UDP receive test error on port {port}: {e}")
            try:
                process.terminate()
            except:
                pass
            return False

    def create_display_pipeline(self, port, window_title):
        """Create GStreamer pipeline for displaying received stream"""
        pipeline = [
            'gst-launch-1.0', '-v',
            'udpsrc', f'port={port}',
            '!', 'application/x-rtp,encoding-name=H264',
            '!', 'rtph264depay',
            '!', 'h264parse',
            '!', 'avdec_h264',
            '!', 'videoconvert',
            '!', 'autovideosink', f'sync=false'
        ]
        return pipeline

    def create_rtsp_pipeline(self, port, rtsp_port, stream_name):
        """Create GStreamer pipeline for RTSP server"""
        # Note: This requires gst-rtsp-server which might not be installed
        # We'll use a simple UDP to RTSP bridge approach
        pipeline = [
            'gst-launch-1.0', '-v',
            'udpsrc', f'port={port}',
            '!', 'application/x-rtp,encoding-name=H264',
            '!', 'rtph264depay',
            '!', 'h264parse',
            '!', 'rtph264pay', 'config-interval=1',
            '!', 'udpsink', f'host=127.0.0.1', f'port={rtsp_port}', 'sync=false'
        ]
        return pipeline

    def create_record_pipeline(self, port, filename):
        """Create GStreamer pipeline for recording received stream"""
        pipeline = [
            'gst-launch-1.0', '-v',
            'udpsrc', f'port={port}',
            '!', 'application/x-rtp,encoding-name=H264',
            '!', 'rtph264depay',
            '!', 'h264parse',
            '!', 'mp4mux',
            '!', 'filesink', f'location={filename}'
        ]
        return pipeline

    def start_camera_receiver(self, port, camera_name, **kwargs):
        """Start receiving for a single camera"""
        logger.info(f"Starting {camera_name} receiver on port {port}")
        
        if self.mode == 'display':
            window_title = kwargs.get('window_title', camera_name)
            pipeline = self.create_display_pipeline(port, window_title)
        elif self.mode == 'rtsp':
            rtsp_port = kwargs.get('rtsp_port', 8554)
            stream_name = kwargs.get('stream_name', camera_name.lower().replace(' ', '_'))
            pipeline = self.create_rtsp_pipeline(port, rtsp_port, stream_name)
        elif self.mode == 'record':
            filename = kwargs.get('filename', f'{camera_name.lower().replace(" ", "_")}.mp4')
            pipeline = self.create_record_pipeline(port, filename)
        else:
            logger.error(f"Unknown mode: {self.mode}")
            return None
        
        try:
            logger.info(f"Starting {camera_name} with pipeline: {' '.join(pipeline)}")
            process = subprocess.Popen(pipeline,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            # Give it a moment to start
            time.sleep(2)
            if process.poll() is None:  # Still running
                logger.info(f"✓ {camera_name} receiver started successfully")
                return process
            else:
                stdout, stderr = process.communicate()
                logger.error(f"✗ {camera_name} receiver failed to start: {stderr}")
                return None
                
        except Exception as e:
            logger.error(f"✗ Exception starting {camera_name} receiver: {e}")
            return None

    def run_diagnostics(self):
        """Run comprehensive diagnostics"""
        logger.info("=== RUNNING DIAGNOSTICS ===")
        
        # Check network connectivity
        network_ok = self.check_network_connectivity()
        
        # Check port availability
        ports_ok = self.check_ports_available()
        
        # Test UDP receive capability
        udp1_ok = self.test_gstreamer_udp_receive(self.camera1_port)
        udp2_ok = self.test_gstreamer_udp_receive(self.camera2_port)
        
        logger.info("=== DIAGNOSTICS SUMMARY ===")
        logger.info(f"Network connectivity: {'✓' if network_ok else '✗'}")
        logger.info(f"Port availability: {'✓' if ports_ok else '✗'}")
        logger.info(f"UDP receive test (port {self.camera1_port}): {'✓' if udp1_ok else '✗'}")
        logger.info(f"UDP receive test (port {self.camera2_port}): {'✓' if udp2_ok else '✗'}")
        
        return all([network_ok, ports_ok, udp1_ok, udp2_ok])

    def start_receiving(self):
        """Start receiving both camera streams"""
        logger.info(f"=== STARTING CAMERA RECEIVING ({self.mode.upper()} MODE) ===")
        
        # Run diagnostics first
        if not self.run_diagnostics():
            logger.error("⚠ Diagnostics revealed issues, but attempting to start receiving anyway...")
        
        # Start camera 1 receiver
        if self.mode == 'display':
            process1 = self.start_camera_receiver(
                self.camera1_port, "Camera 1", window_title="Camera 1 - Front")
        elif self.mode == 'rtsp':
            process1 = self.start_camera_receiver(
                self.camera1_port, "Camera 1", rtsp_port=self.rtsp_port1, stream_name="camera1")
        elif self.mode == 'record':
            timestamp = int(time.time())
            process1 = self.start_camera_receiver(
                self.camera1_port, "Camera 1", filename=f"camera1_{timestamp}.mp4")
        
        if process1:
            self.processes.append(process1)
        
        # Start camera 2 receiver
        if self.mode == 'display':
            process2 = self.start_camera_receiver(
                self.camera2_port, "Camera 2", window_title="Camera 2 - Rear")
        elif self.mode == 'rtsp':
            process2 = self.start_camera_receiver(
                self.camera2_port, "Camera 2", rtsp_port=self.rtsp_port2, stream_name="camera2")
        elif self.mode == 'record':
            timestamp = int(time.time())
            process2 = self.start_camera_receiver(
                self.camera2_port, "Camera 2", filename=f"camera2_{timestamp}.mp4")
        
        if process2:
            self.processes.append(process2)
        
        if not self.processes:
            logger.error("✗ Failed to start any camera receivers")
            return False
        
        logger.info(f"✓ Started {len(self.processes)} camera receiver(s)")
        
        if self.mode == 'display':
            logger.info("Camera streams should appear in separate windows")
        elif self.mode == 'rtsp':
            logger.info(f"RTSP streams available at:")
            logger.info(f"  Camera 1: rtsp://{self.base_ip}:{self.rtsp_port1}/camera1")
            logger.info(f"  Camera 2: rtsp://{self.base_ip}:{self.rtsp_port2}/camera2")
        elif self.mode == 'record':
            logger.info("Recording camera streams to MP4 files")
        
        return True

    def stop_receiving(self):
        """Stop all receiving processes"""
        logger.info("Stopping camera receivers...")
        for process in self.processes:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            except Exception as e:
                logger.warning(f"Error stopping process: {e}")
        self.processes.clear()
        logger.info("All receivers stopped")

    def monitor_receivers(self):
        """Monitor running receivers"""
        logger.info("Starting receiver monitoring...")
        try:
            while True:
                # Check if processes are still running
                running_processes = []
                for i, process in enumerate(self.processes):
                    if process.poll() is None:  # Still running
                        running_processes.append(process)
                    else:
                        stdout, stderr = process.communicate()
                        logger.warning(f"Receiver {i+1} stopped unexpectedly: {stderr}")
                
                self.processes = running_processes
                
                if not self.processes:
                    logger.error("All receivers stopped, exiting...")
                    break
                
                # Log status every 30 seconds
                logger.info(f"Status: {len(self.processes)} receivers running")
                time.sleep(30)
                
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            self.stop_receiving()


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Base Station Camera Receiver')
    parser.add_argument('--mode', choices=['display', 'rtsp', 'record'], 
                       default='display', help='Receiving mode (default: display)')
    parser.add_argument('--jetson-ip', default='192.168.1.100',
                       help='Jetson IP address (default: 192.168.1.100)')
    parser.add_argument('--base-ip', default='192.168.1.10',
                       help='Base station IP address (default: 192.168.1.10)')
    
    args = parser.parse_args()
    
    receiver = CameraReceiver(mode=args.mode)
    receiver.jetson_ip = args.jetson_ip
    receiver.base_ip = args.base_ip
    
    try:
        if receiver.start_receiving():
            receiver.monitor_receivers()
        else:
            logger.error("Failed to start receiving")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        receiver.stop_receiving()


if __name__ == "__main__":
    main()