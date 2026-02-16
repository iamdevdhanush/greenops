"""
GreenOps Agent - Idle Detection
Platform-specific implementations for detecting user inactivity
"""
import platform
import logging

logger = logging.getLogger(__name__)

class IdleDetector:
    """Detect user idle time across platforms"""
    
    def __init__(self):
        self.platform = platform.system()
        logger.info(f"Idle detector initialized for platform: {self.platform}")
    
    def get_idle_seconds(self) -> int:
        """
        Get seconds since last user input
        
        Returns:
            Seconds of idle time, or 0 if detection fails
        """
        try:
            if self.platform == 'Windows':
                return self._get_idle_windows()
            elif self.platform == 'Linux':
                return self._get_idle_linux()
            elif self.platform == 'Darwin':  # macOS
                return self._get_idle_macos()
            else:
                logger.warning(f"Unsupported platform: {self.platform}")
                return 0
        except Exception as e:
            logger.error(f"Failed to detect idle time: {e}")
            return 0
    
    def _get_idle_windows(self) -> int:
        """
        Windows: Use GetLastInputInfo via ctypes
        
        Returns milliseconds since last input event
        """
        try:
            import ctypes
            from ctypes import Structure, windll, c_uint, sizeof, byref
            
            class LASTINPUTINFO(Structure):
                _fields_ = [
                    ('cbSize', c_uint),
                    ('dwTime', c_uint)
                ]
            
            lii = LASTINPUTINFO()
            lii.cbSize = sizeof(LASTINPUTINFO)
            
            # Get last input time
            windll.user32.GetLastInputInfo(byref(lii))
            
            # Get current tick count
            millis_since_input = windll.kernel32.GetTickCount() - lii.dwTime
            
            return int(millis_since_input / 1000.0)
            
        except Exception as e:
            logger.error(f"Windows idle detection failed: {e}")
            return 0
    
    def _get_idle_linux(self) -> int:
        """
        Linux: Try multiple methods
        1. xprintidle (most accurate for X11)
        2. Parse /proc/stat (fallback)
        3. Return 0 if all fail
        """
        # Method 1: xprintidle (requires X11)
        try:
            import subprocess
            result = subprocess.run(
                ['xprintidle'],
                capture_output=True,
                text=True,
                timeout=1
            )
            if result.returncode == 0:
                idle_ms = int(result.stdout.strip())
                return int(idle_ms / 1000.0)
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        
        # Method 2: Check X11 via python-xlib
        try:
            from Xlib import display
            from Xlib.ext import dpms
            
            d = display.Display()
            info = dpms.get_timeouts(d)
            # This is not perfect but better than nothing
            # TODO: Implement proper X11 idle detection
            return 0
        except ImportError:
            pass
        
        # Method 3: Fallback - no reliable way without X11
        logger.warning("No reliable idle detection available on this Linux system. "
                      "Install xprintidle: sudo apt-get install xprintidle")
        return 0
    
    def _get_idle_macos(self) -> int:
        """
        macOS: Use IOKit to get HIDIdleTime
        """
        try:
            import subprocess
            
            # Use ioreg to query HIDIdleTime
            result = subprocess.run(
                ['ioreg', '-c', 'IOHIDSystem'],
                capture_output=True,
                text=True,
                timeout=1
            )
            
            if result.returncode == 0:
                output = result.stdout
                
                # Parse HIDIdleTime (in nanoseconds)
                for line in output.split('\n'):
                    if 'HIDIdleTime' in line:
                        # Format: "HIDIdleTime" = 12345678900
                        idle_ns = int(line.split('=')[1].strip())
                        idle_seconds = idle_ns / 1_000_000_000
                        return int(idle_seconds)
            
            logger.warning("Could not parse HIDIdleTime from ioreg")
            return 0
            
        except Exception as e:
            logger.error(f"macOS idle detection failed: {e}")
            return 0
