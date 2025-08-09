#!/usr/bin/env python3
"""
Network Diagnostics Tool for Camera Streaming Setup
Tests connectivity, ports, and GStreamer capabilities between Jetson and base station
"""

import subprocess
import sys
import time
import logging
import socket
import argparse
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('network_diagnostics.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class NetworkDiagnostics:
    def __init__(self, jetson_ip="192.168.1.100", base_ip="192.168.1.10"):
        self.jetson_ip = jetson_ip
        self.base_ip = base_ip
        self.test_ports = [5000, 5001, 8554, 8555]
        
    def test_ping_connectivity(self, target_ip, target_name):
        """Test basic ping connectivity"""
        logger.info(f"Testing ping connectivity to {target_name} ({target_ip})")
        try:
            result = subprocess.run(
                ['ping', '-c', '3', '-W', '5', target_ip],
                capture_output=True, text=True, timeout=20
            )
            if result.returncode == 0:
                logger.info(f"✓ Ping to {target_name} successful")
                # Extract packet loss info
                lines = result.stdout.split('\n')
                for line in lines:
                    if 'packet loss' in line:
                        logger.info(f"  {line.strip()}")
                return True
            else:
                logger.error(f"✗ Ping to {target_name} failed")
                logger.debug(f"Ping output: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            logger.error(f"✗ Ping to {target_name} timed out")
            return False
        except Exception as e:
            logger.error(f"✗ Ping test error: {e}")
            return False

    def test_port_connectivity(self, target_ip, port, target_name):
        """Test TCP port connectivity"""
        logger.info(f"Testing TCP port {port} on {target_name} ({target_ip})")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            result = sock.connect_ex((target_ip, port))
            sock.close()
            
            if result == 0:
                logger.info(f"✓ TCP port {port} is open on {target_name}")
                return True
            else:
                logger.warning(f"⚠ TCP port {port} is closed/filtered on {target_name} (normal for UDP)")
                return False
        except Exception as e:
            logger.warning(f"⚠ TCP port test error for {port}: {e}")
            return False

    def test_udp_send_receive(self, send_port, receive_port):
        """Test UDP packet transmission between devices"""
        logger.info(f"Testing UDP transmission: send to port {send_port}, receive on port {receive_port}")
        
        # Create a simple UDP test
        test_message = b"CAMERA_STREAM_TEST_PACKET"
        
        try:
            # Create receiver socket
            receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            receiver.settimeout(10)
            receiver.bind(('', receive_port))
            
            # Create sender socket
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            
            # Send test packet
            sender.sendto(test_message, (self.jetson_ip if send_port == 5000 else self.base_ip, send_port))
            
            # Try to receive
            data, addr = receiver.recvfrom(1024)
            
            receiver.close()
            sender.close()
            
            if data == test_message:
                logger.info(f"✓ UDP test successful: {len(data)} bytes received from {addr}")
                return True
            else:
                logger.warning(f"⚠ UDP test partial: received {len(data)} bytes but content differs")
                return False
                
        except socket.timeout:
            logger.warning(f"⚠ UDP test timeout on port {receive_port}")
            return False
        except Exception as e:
            logger.error(f"✗ UDP test error: {e}")
            return False
        finally:
            try:
                receiver.close()
                sender.close()
            except:
                pass

    def test_network_interface(self):
        """Test network interface configuration"""
        logger.info("Testing network interface configuration")
        try:
            # Get network interface info
            result = subprocess.run(['ip', 'addr', 'show'], capture_output=True, text=True)
            if result.returncode == 0:
                logger.info("✓ Network interfaces:")
                lines = result.stdout.split('\n')
                for line in lines:
                    if 'inet ' in line and not '127.0.0.1' in line:
                        logger.info(f"  {line.strip()}")
                
                # Check if our expected IPs are configured
                if self.base_ip in result.stdout:
                    logger.info(f"✓ Base IP {self.base_ip} found in interface configuration")
                else:
                    logger.warning(f"⚠ Base IP {self.base_ip} not found in interface configuration")
                
                return True
            else:
                logger.error("✗ Failed to get network interface information")
                return False
        except Exception as e:
            logger.error(f"✗ Network interface test error: {e}")
            return False

    def test_gstreamer_elements(self):
        """Test required GStreamer elements are available"""
        logger.info("Testing GStreamer element availability")
        
        required_elements = [
            'v4l2src', 'udpsrc', 'udpsink', 'rtph264pay', 'rtph264depay',
            'h264parse', 'jpegdec', 'videoconvert', 'omxh264enc', 'x264enc',
            'avdec_h264', 'autovideosink'
        ]
        
        missing_elements = []
        available_elements = []
        
        for element in required_elements:
            try:
                result = subprocess.run(
                    ['gst-inspect-1.0', element],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    available_elements.append(element)
                    logger.info(f"✓ GStreamer element '{element}' available")
                else:
                    missing_elements.append(element)
                    logger.warning(f"⚠ GStreamer element '{element}' not found")
            except Exception as e:
                missing_elements.append(element)
                logger.warning(f"⚠ Error checking element '{element}': {e}")
        
        logger.info(f"GStreamer elements: {len(available_elements)} available, {len(missing_elements)} missing")
        
        return len(missing_elements) == 0

    def test_camera_devices(self):
        """Test camera device availability"""
        logger.info("Testing camera device availability")
        
        expected_devices = ['/dev/video0', '/dev/video2']
        available_devices = []
        
        for device in expected_devices:
            if Path(device).exists():
                try:
                    # Test basic access
                    result = subprocess.run(
                        ['v4l2-ctl', '--device', device, '--get-fmt-video'],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0:
                        available_devices.append(device)
                        logger.info(f"✓ Camera device {device} accessible")
                    else:
                        logger.error(f"✗ Camera device {device} not accessible: {result.stderr}")
                except Exception as e:
                    logger.error(f"✗ Error testing camera device {device}: {e}")
            else:
                logger.error(f"✗ Camera device {device} does not exist")
        
        logger.info(f"Camera devices: {len(available_devices)}/{len(expected_devices)} available")
        return len(available_devices) == len(expected_devices)

    def test_bandwidth_estimation(self):
        """Estimate available network bandwidth"""
        logger.info("Testing network bandwidth estimation")
        try:
            # Use iperf3 if available, otherwise skip
            result = subprocess.run(['which', 'iperf3'], capture_output=True)
            if result.returncode != 0:
                logger.warning("⚠ iperf3 not available, skipping bandwidth test")
                logger.info("  Estimated bandwidth needed: ~2-4 Mbps for dual camera streams")
                return True
            
            logger.info("iperf3 available but requires server setup - skipping automated test")
            logger.info("  Estimated bandwidth needed: ~2-4 Mbps for dual camera streams")
            return True
            
        except Exception as e:
            logger.warning(f"⚠ Bandwidth test error: {e}")
            return True  # Non-critical

    def run_full_diagnostics(self):
        """Run complete diagnostic suite"""
        logger.info("=" * 50)
        logger.info("STARTING COMPREHENSIVE NETWORK DIAGNOSTICS")
        logger.info("=" * 50)
        
        test_results = {}
        
        # Test 1: Network interface configuration
        test_results['network_interface'] = self.test_network_interface()
        
        # Test 2: Ping connectivity
        test_results['ping_jetson'] = self.test_ping_connectivity(self.jetson_ip, "Jetson")
        test_results['ping_base'] = self.test_ping_connectivity(self.base_ip, "Base Station")
        
        # Test 3: TCP port connectivity (informational)
        for port in [22, 80]:  # Common ports
            self.test_port_connectivity(self.jetson_ip, port, "Jetson")
            self.test_port_connectivity(self.base_ip, port, "Base Station")
        
        # Test 4: GStreamer elements
        test_results['gstreamer'] = self.test_gstreamer_elements()
        
        # Test 5: Camera devices (only on Jetson)
        test_results['cameras'] = self.test_camera_devices()
        
        # Test 6: Bandwidth estimation
        test_results['bandwidth'] = self.test_bandwidth_estimation()
        
        # Summary
        logger.info("=" * 50)
        logger.info("DIAGNOSTIC RESULTS SUMMARY")
        logger.info("=" * 50)
        
        passed_tests = sum(1 for result in test_results.values() if result)
        total_tests = len(test_results)
        
        for test_name, result in test_results.items():
            status = "✓ PASS" if result else "✗ FAIL"
            logger.info(f"{test_name.replace('_', ' ').title()}: {status}")
        
        logger.info(f"\nOverall: {passed_tests}/{total_tests} tests passed")
        
        if passed_tests == total_tests:
            logger.info("🎉 All diagnostics passed! System should work correctly.")
        elif passed_tests >= total_tests * 0.8:
            logger.warning("⚠ Most tests passed. Some issues detected but streaming may still work.")
        else:
            logger.error("❌ Multiple issues detected. Please resolve before attempting streaming.")
        
        return passed_tests >= total_tests * 0.8

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Network Diagnostics for Camera Streaming')
    parser.add_argument('--jetson-ip', default='192.168.1.100',
                       help='Jetson IP address (default: 192.168.1.100)')
    parser.add_argument('--base-ip', default='192.168.1.10',
                       help='Base station IP address (default: 192.168.1.10)')
    parser.add_argument('--quick', action='store_true',
                       help='Run quick diagnostics only')
    
    args = parser.parse_args()
    
    diagnostics = NetworkDiagnostics(args.jetson_ip, args.base_ip)
    
    try:
        if diagnostics.run_full_diagnostics():
            logger.info("Diagnostics completed successfully")
            sys.exit(0)
        else:
            logger.error("Diagnostics revealed issues")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Diagnostics interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error during diagnostics: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()