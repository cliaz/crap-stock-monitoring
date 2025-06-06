#!/usr/bin/env python3

import requests
import cv2
import numpy as np
from PIL import Image
from io import BytesIO  # Required for image download
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import time
import re
import os
import random
import smtplib
import sys
import argparse

# Check for required pytz dependency
try:
    import pytz
except ImportError:
    print("Error: pytz module not found. Please install it using:")
    print("pip install pytz")
    sys.exit(1)


class ChartWebInteraction:
    """Handles all web interactions and image downloads"""
    
    def __init__(self, symbol):
        self.symbol = symbol
        self.base_url = f"https://stockcharts.com/sc3/ui/?s={symbol}"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
    
    def get_current_chart_url(self):
        """Generate the current chart URL using StockCharts' direct API"""
        # Generate parameters similar to what StockCharts uses
        random_id = random.randint(1000000000, 9999999999)  # 10-digit random number
        timestamp = int(time.time() * 1000)  # Current timestamp in milliseconds
        
        # URL encode the $ if present
        symbol_encoded = self.symbol.replace('$', '%24')
        chart_url = f"https://stockcharts.com/c-sc/sc?s={symbol_encoded}&p=D&yr=1&mn=0&dy=0&i=t{random_id}c&r={timestamp}"
        
        return chart_url
        
    def download_chart_image(self):
        """Download the chart image from StockCharts"""
        chart_url = self.get_current_chart_url()
        print(f"Trying chart URL: {chart_url}")
        
        try:
            response = self.session.get(chart_url, timeout=15)
            response.raise_for_status()
            
            # Check if we got an image
            content_type = response.headers.get('content-type', '')
            if 'image' not in content_type:
                print(f"Response is not an image: {content_type}")
                print(f"Response length: {len(response.content)}")
                print(f"Status code: {response.status_code}")
                return None
            
            print(f"✅ Successfully downloaded image from: {chart_url}")
            
            # Convert to PIL Image
            image = Image.open(BytesIO(response.content))
            return image
            
        except requests.exceptions.RequestException as e:
            print(f"Error downloading image from {chart_url}: {e}")
            return None
        except Exception as e:
            print(f"Error processing image: {e}")
            return None


class ImageProcessor:
    """Handles image processing and analysis"""
    
    # Default analysis region coordinates
    DEFAULT_START_Y = 125
    DEFAULT_END_Y = 403
    DEFAULT_START_X = 620
    DEFAULT_END_X = 650
    
    # Image comparison threshold
    IMAGE_DIFF_THRESHOLD = 100
    
    # Color detection thresholds
    RED_HUE_LOWER1 = np.array([0, 30, 30])
    RED_HUE_UPPER1 = np.array([15, 255, 255])
    RED_HUE_LOWER2 = np.array([165, 30, 30])
    RED_HUE_UPPER2 = np.array([180, 255, 255])
    BLACK_HUE_LOWER = np.array([0, 0, 0])
    BLACK_HUE_UPPER = np.array([180, 150, 100])
    
    def save_debug_image(self, image, filename, debug_mode=False):
        """Save an image to disk for debugging purposes - only if debug_mode is True"""
        if not debug_mode:
            return
            
        try:
            success = cv2.imwrite(filename, image)
            if success:
                print(f"💾 Image saved as: {filename}")
            else:
                print(f"❌ Failed to save image: {filename}")
        except Exception as e:
            print(f"❌ Error saving image {filename}: {e}")
    
    def extract_middle_section(self, image, start_x=None, end_x=None, start_y=None, end_y=None):
        """Extract the middle section of the chart where the line is located"""
        if image is None:
            return None
        
        height, width = image.shape[:2]
        
        # Define middle section with specific pixel measurements
        # Use provided vertical bounds if specified, otherwise use defaults
        if start_y is None:
            start_y = self.DEFAULT_START_Y
        if end_y is None:
            end_y = self.DEFAULT_END_Y
        
        # Use provided horizontal bounds if specified, otherwise use defaults
        if start_x is None or end_x is None:
            # Default: analyze pixels from defined constants
            start_x = min(self.DEFAULT_START_X, width)
            end_x = min(self.DEFAULT_END_X, width)
        else:
            # Ensure we don't exceed image dimensions
            start_x = max(0, min(start_x, width - 1))
            end_x = max(start_x + 1, min(end_x, width))
        
        # Ensure we don't go out of bounds
        start_y = max(0, min(start_y, height - 1))
        end_y = max(start_y + 1, min(end_y, height))
        
        middle_section = image[start_y:end_y, start_x:end_x]
        return middle_section
    
    def get_rightmost_column_color(self, column_colors):
        """Get the color of the rightmost column that isn't 'unknown'"""
        if not column_colors:
            return "unknown"
            
        # Start from the rightmost column and go backwards
        for color in reversed(column_colors):
            if color != "unknown":
                return color
                
        return "unknown"
    
    def detect_line_crossing(self, image_section, debug=False):
        """
        Detect if a contiguous line changes color from red to black or black to red.
        
        Returns:
        - "red_to_black": If line changes from red to black
        - "black_to_red": If line changes from black to red
        - "no_crossing": If no color transition is detected
        - None: If analysis failed
        
        When debug=True, also returns color data for detailed analysis.
        """
        if image_section is None:
            return None
            
        # Convert to HSV for better color detection
        hsv = cv2.cvtColor(image_section, cv2.COLOR_BGR2HSV)
        
        # Define color ranges for red and black
        red_lower1 = self.RED_HUE_LOWER1
        red_upper1 = self.RED_HUE_UPPER1
        red_lower2 = self.RED_HUE_LOWER2
        red_upper2 = self.RED_HUE_UPPER2
        
        # Relaxed thresholds for black to detect faint black pixels
        black_lower = self.BLACK_HUE_LOWER
        black_upper = self.BLACK_HUE_UPPER
        
        # Create masks
        red_mask1 = cv2.inRange(hsv, red_lower1, red_upper1)
        red_mask2 = cv2.inRange(hsv, red_lower2, red_upper2)
        red_mask = cv2.bitwise_or(red_mask1, red_mask2)
        
        black_mask = cv2.inRange(hsv, black_lower, black_upper)
        
        # Get image dimensions
        height, width = image_section.shape[:2]
        
        # We'll analyze column by column to detect horizontal transitions
        # For each column, we'll determine the dominant color
        column_colors = []
        column_data = []  # Store detailed pixel counts for debug mode
        
        # Analyze middle 50% of height to focus on the main chart line
        middle_top = height // 4
        middle_bottom = 3 * height // 4
        
        for x in range(width):
            # Count red and black pixels in this column (middle section)
            col_red_pixels = cv2.countNonZero(red_mask[middle_top:middle_bottom, x:x+1])
            col_black_pixels = cv2.countNonZero(black_mask[middle_top:middle_bottom, x:x+1])
            
            # Adjust for the fixed red line that's present in every column (subtract 1 from red pixel count)
            adjusted_red_pixels = max(0, col_red_pixels - 1)
            
            # Store detailed pixel counts for debug mode
            if debug:
                # Calculate a color strength ratio for easier analysis
                red_ratio = 0
                if col_black_pixels > 0:
                    red_ratio = adjusted_red_pixels / col_black_pixels if col_black_pixels > 0 else float('inf')
                else:
                    red_ratio = float('inf') if adjusted_red_pixels > 0 else 0
                
                column_data.append({
                    "column": x,
                    "red_pixels_raw": int(col_red_pixels),
                    "red_pixels": int(adjusted_red_pixels),  # Store adjusted value
                    "black_pixels": int(col_black_pixels),
                    "total_analyzed_pixels": middle_bottom - middle_top,
                    "red_percentage": int(adjusted_red_pixels / (middle_bottom - middle_top) * 100) if (middle_bottom - middle_top) > 0 else 0,
                    "black_percentage": int(col_black_pixels / (middle_bottom - middle_top) * 100) if (middle_bottom - middle_top) > 0 else 0,
                    "red_to_black_ratio": red_ratio  # Higher ratio means more red relative to black
                })
            
            # Determine dominant color for this column using adjusted red pixel count
            if adjusted_red_pixels > 0 and adjusted_red_pixels >= col_black_pixels:
                column_colors.append("red")
            elif col_black_pixels > 0 and col_black_pixels >= adjusted_red_pixels:
                column_colors.append("black")
            else:
                column_colors.append("unknown")
        
        if debug:
            # Create a visualization of the column colors
            color_map = image_section.copy()
            for x, color in enumerate(column_colors):
                if color == "red":
                    color_map[:, x] = [0, 0, 255]  # Red in BGR
                elif color == "black":
                    color_map[:, x] = [0, 0, 0]    # Black
                else:
                    color_map[:, x] = [128, 128, 128]  # Gray for unknown
            
            # Mark transition points with vertical lines
            for i in range(1, len(column_colors)):
                if (column_colors[i-1] == "red" and column_colors[i] == "black") or \
                   (column_colors[i-1] == "black" and column_colors[i] == "red"):
                    # Add a bright green vertical line at transition point
                    color_map[:, i] = [0, 255, 0]  # Green in BGR
                    
            self.save_debug_image(color_map, "color_transition_map.png", debug)
        
        # Analyze the color transitions
        # We need at least some columns of each color to detect a crossing
        if "red" not in column_colors or "black" not in column_colors:
            if debug:
                print("DEBUG: No transition possible - only one color present")
            if debug:
                return "no_crossing", {
                    "column_colors": column_colors,
                    "column_data": column_data,
                    "rightmost_color": self.get_rightmost_column_color(column_colors),
                    "transitions": {
                        "red_to_black": [],
                        "black_to_red": []
                    }
                }
            return "no_crossing", self.get_rightmost_column_color(column_colors)
        
        # Track transitions with their positions
        red_to_black_transitions = []
        black_to_red_transitions = []
        
        for i in range(1, len(column_colors)):
            if column_colors[i-1] == "red" and column_colors[i] == "black":
                red_to_black_transitions.append(i)
            elif column_colors[i-1] == "black" and column_colors[i] == "red":
                black_to_red_transitions.append(i)
        
        # Count transitions
        red_to_black_count = len(red_to_black_transitions)
        black_to_red_count = len(black_to_red_transitions)
        
        # Determine the dominant transition
        if red_to_black_count > black_to_red_count and red_to_black_count > 0:
            result = "red_to_black"
        elif black_to_red_count > red_to_black_count and black_to_red_count > 0:
            result = "black_to_red"
        else:
            # If there are equal transitions or no clear pattern
            result = "no_crossing"
        
        # Get the color of the rightmost column
        rightmost_color = self.get_rightmost_column_color(column_colors)
        
        if debug:
            # Return detailed data for test mode
            return result, {
                "column_colors": column_colors,
                "column_data": column_data,
                "rightmost_color": rightmost_color,
                "transitions": {
                    "red_to_black": red_to_black_transitions,
                    "black_to_red": black_to_red_transitions
                }
            }
        
        # For normal mode, return a tuple of (crossing type, rightmost color)
        return result, rightmost_color
    
    def images_are_different(self, img1, img2):
        """
        Compare two images to check if they are different.
        
        Args:
            img1: First image (OpenCV format)
            img2: Second image (OpenCV format)
            
        Returns:
            bool: True if images are different, False if they are the same
        """
        # If either image is None, they are considered different
        if img1 is None or img2 is None:
            return True
            
        # Check if image dimensions match
        if img1.shape != img2.shape:
            return True
            
        # Calculate the difference
        diff = cv2.absdiff(img1, img2)
        
        # If any pixel is different, the images are different
        non_zero_count = cv2.countNonZero(cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY))
        return non_zero_count > self.IMAGE_DIFF_THRESHOLD


class NotificationManager:
    """Handles logging and notifications"""
    
    def __init__(self, symbol):
        self.symbol = symbol
        # Generate log file name based on symbol (without $ character)
        self.log_file = f"logs/{symbol.replace('$', '')}_changes.log"
    
    def log_transition(self, message, crossing_type, send_email=True, silent=True):
        """
        Log a transition event to a file with timestamp and send email notification
        
        Args:
            message (str): The message to log
            crossing_type (str): The type of crossing detected (e.g., 'red_to_black', 'black_to_red')
            send_email (bool): Whether to send an email notification (default: True)
            silent (bool): Whether to suppress logging message to console (default: True)
        """
        log_file = self.log_file
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Remove emojis and other special characters from message for log file
        clean_message = re.sub(r'[^\x00-\x7F]+', '', message)
        
        # Split multi-line messages and add timestamp to each line
        lines = clean_message.split('\n')
        formatted_lines = []
        
        for i, line in enumerate(lines):
            # Only the first line gets the original timestamp
            if i == 0:
                formatted_lines.append(f"[{timestamp}] {line}")
            else:
                # Add timestamp to additional lines if they exist
                formatted_lines.append(f"[{timestamp}] {line}")
        
        # Join the formatted lines back together
        log_entry = '\n'.join(formatted_lines)
        
        # Write to log file
        with open(log_file, 'a') as f:
            f.write(log_entry + '\n')
        
        if not silent:
            print(f"📝 Logged event to {log_file}")
        
        # Send email notification if requested
        if send_email:
            self.send_email_notification(message, crossing_type)
    
    def send_email_notification(self, message, crossing_type):
        """
        Send email notification about a transition
        """
        # Try to import email details from email_details.py
        try:
            # Import variables directly from email_details.py
            from email_details import sender_email, sender_password_password as sender_password, recipients
            
            # Check if email credentials are configured
            if not sender_email or not sender_password:
                print("⚠️ Email notification skipped: Missing email credentials")
                return
                
            if not recipients:
                print("⚠️ No recipients specified in email_details.py")
                print("Using sender as recipient")
                recipients = [sender_email]
            
            # Create email
            subject = f"{self.symbol} Alert: {crossing_type.replace('_', ' ').title()} Transition Detected"
            
            # Create the email content
            msg = MIMEMultipart()
            msg['From'] = sender_email
            msg['To'] = ", ".join(recipients)
            msg['Subject'] = subject
            
            # Create the email body with HTML formatting
            html = f"""
            <html>
            <body>
                <h2>Stock Chart Transition Alert</h2>
                <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p><strong>Symbol:</strong> {self.symbol}</p>
                <p><strong>Event:</strong> {crossing_type.replace('_', ' ').title()} Transition</p>
                <p><strong>Details:</strong> {message}</p>
                <p>This is an automated alert from your Stock Chart Monitor.</p>
            </body>
            </html>
            """
            
            msg.attach(MIMEText(html, 'html'))
            
            try:
                # Connect to Gmail SMTP server
                server = smtplib.SMTP('smtp.gmail.com', 587)
                server.starttls()
                
                # Login to email account
                server.login(sender_email, sender_password)
                
                # Send email
                server.send_message(msg)
                server.quit()
                
                # Log the email notification
                email_log_message = f"Email notification sent to {', '.join(recipients)}"
                print(f"📧 {email_log_message}")
                
                # Add an entry to the log file for the email
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                clean_email_log = re.sub(r'[^\x00-\x7F]+', '', email_log_message)
                with open(self.log_file, 'a') as f:
                    f.write(f"[{timestamp}] EMAIL SENT: {clean_email_log}\n")
            except Exception as e:
                error_message = f"Failed to send email notification: {e}"
                print(f"❌ {error_message}")
                
                # Log the email failure
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                clean_error = re.sub(r'[^\x00-\x7F]+', '', error_message)
                with open(self.log_file, 'a') as f:
                    f.write(f"[{timestamp}] EMAIL ERROR: {clean_error}\n")
                
        except ImportError as e:
            print(f"⚠️ Error importing email configuration: {e}")
            print("Please make sure email_details.py is properly configured")
        except Exception as e:
            print(f"⚠️ Error in email configuration: {e}")
    
    def get_last_logged_crossing(self):
        """
        Get the last line crossing event from the log file
        
        Returns:
            str: The last crossing event type, or "no_crossing" if none found
        """
        log_file = self.log_file
        
        # Check if log file exists
        if not os.path.exists(log_file):
            return "no_crossing"
            
        try:
            # Read the last line of the log file
            with open(log_file, 'r') as f:
                lines = f.readlines()
                
            if not lines:
                return "no_crossing"
                
            # Find the most recent line with a crossing event (not a color update)
            for line in reversed(lines):
                if "Line crossing detected:" in line:
                    # Extract the crossing type from the log entry
                    match = re.search(r"Line crossing detected: (red_to_black|black_to_red)", line)
                    if match:
                        return match.group(1)
            
            # If no crossing detected in log
            return "no_crossing"
            
        except Exception as e:
            print(f"⚠️ Error reading log file: {e}")
            return "no_crossing"
    
    def should_log_transition(self, current_crossing):
        """
        Determine if a crossing should be logged based on previous log entries
        
        Rules:
        1. Always log if it's a different crossing type than the last one
        2. Only log if we haven't already logged this crossing type today
        
        Args:
            current_crossing: The current crossing type detected
            
        Returns:
            bool: True if we should log this crossing, False otherwise
        """
        # No need to log if no crossing detected
        if current_crossing == "no_crossing":
            return False
            
        # Always log if different from last crossing
        last_crossing = self.get_last_logged_crossing()
        if last_crossing != current_crossing:
            return True
            
        # Check if we've already logged this crossing type today
        log_file = self.log_file
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Check if log file exists
        if not os.path.exists(log_file):
            return True
            
        try:
            # Read all lines in the log file
            with open(log_file, 'r') as f:
                lines = f.readlines()
                
            # Look for entries from today with the same crossing type
            today_pattern = f"\\[{today}"
            crossing_pattern = f"Line crossing detected: {current_crossing}"
            
            for line in lines:
                if re.search(today_pattern, line) and crossing_pattern in line:
                    # Already logged this crossing type today
                    return False
                    
            # If we get here, we haven't logged this crossing type today
            return True
            
        except Exception as e:
            print(f"⚠️ Error checking log file: {e}")
            # If there's an error, log anyway
            return True


class StockChartMonitor:
    """Main class that integrates all components for stock chart monitoring"""
    
    # Default timezone for monitoring
    TIMEZONE = 'Australia/Sydney'
    
    # Default monitoring window times (AEST)
    MONITORING_START_HOUR = 9
    MONITORING_START_MINUTE = 30
    MONITORING_END_HOUR = 10
    MONITORING_END_MINUTE = 30
    
    def __init__(self, symbol="$NYSI"):
        self.symbol = symbol
        self.web_interaction = ChartWebInteraction(symbol)
        self.image_processor = ImageProcessor()
        self.notification_mgr = NotificationManager(symbol)
        self.last_saved_image = None  # Store the last saved image for comparison
        self.last_saved_image_path = None  # Store the filename of the last saved image
        self._load_most_recent_saved_image()  # Load the most recent image at startup
    
    def _load_most_recent_saved_image(self):
        """Load the most recent chart image from downloaded_charts and set self.last_saved_image (OpenCV format)"""
        import glob
        import cv2
        import numpy as np
        from datetime import datetime
        charts_dir = "downloaded_charts"
        pattern = os.path.join(charts_dir, f"stockcharts_{self.symbol.replace('$', '')}_*.png")
        image_files = glob.glob(pattern)
        if not image_files:
            self.last_saved_image = None
            self.last_saved_image_path = None
            return
        # Sort files by date in filename (descending, using real date)
        def extract_date(f):
            import re
            m = re.search(r"_(\d{4}-\d{2}-\d{2})\.png$", f)  # <-- FIX: use raw string and single backslash
            if m:
                try:
                    return datetime.strptime(m.group(1), "%Y-%m-%d")
                except Exception:
                    return datetime.min
            return datetime.min
        image_files.sort(key=extract_date, reverse=True)
        most_recent = image_files[0]
        try:
            img = cv2.imread(most_recent)
            if img is not None:
                self.last_saved_image = img
                self.last_saved_image_path = most_recent
                print(f"📊 Loaded most recent saved chart image: {most_recent}")
            else:
                print(f"⚠️ Failed to load image: {most_recent}")
                self.last_saved_image = None
                self.last_saved_image_path = None
        except Exception as e:
            print(f"⚠️ Error loading most recent image: {e}")
            self.last_saved_image = None
            self.last_saved_image_path = None

    def download_chart_image(self):
        """Download the chart image from the web"""
        image = self.web_interaction.download_chart_image()
        if image is not None:
            # Log successful image download
            self.notification_mgr.log_transition("Image successfully downloaded", "image_download_success", send_email=False)
        return image
    
    def save_chart_image(self, image):
        """Save the chart image to a file with timestamp"""
        if image is None:
            return None
            
        # Make sure the downloaded_charts directory exists
        charts_dir = "downloaded_charts"
        if not os.path.exists(charts_dir):
            try:
                os.makedirs(charts_dir)
                print(f"Created directory: {charts_dir}")
            except Exception as e:
                print(f"❌ Error creating directory {charts_dir}: {e}")
                # Fall back to current directory if we can't create the folder
                charts_dir = "."
            
        # Generate a filename with date
        clean_symbol = self.symbol.replace('$', '')
        date_str = datetime.now().strftime('%Y-%m-%d')
        filename = f"stockcharts_{clean_symbol}_{date_str}.png"
        filepath = os.path.join(charts_dir, filename)
        
        try:
            # Save the image
            image.save(filepath)
            print(f"💾 Chart image saved as: {filepath}")
            # Log that a new image was saved
            self.notification_mgr.log_transition(f"Image saved as: {filepath}", "new_image_saved", send_email=False)
            return filepath
        except Exception as e:
            print(f"❌ Error saving chart image: {e}")
            return None
    
    def load_todays_image(self):
        """Check if today's image already exists and load it"""
        clean_symbol = self.symbol.replace('$', '')
        date_str = datetime.now().strftime('%Y-%m-%d')
        filename = f"stockcharts_{clean_symbol}_{date_str}.png"
        
        # Check in downloaded_charts directory first
        charts_dir = "downloaded_charts"
        filepath = os.path.join(charts_dir, filename)
        
        if not os.path.exists(filepath):
            # If not in downloaded_charts, check root directory
            if os.path.exists(filename):
                filepath = filename
            else:
                return False
            try:
                # Load the image
                image = cv2.imread(filename)
                print(f"📊 Loaded existing chart image from today: {filename}")
                self.last_saved_image = image
                self.last_saved_image_path = filepath
                return True
            except Exception as e:
                print(f"⚠️ Error loading existing chart image: {e}")
                self.last_saved_image = None
                self.last_saved_image_path = None
                return False
        
        return False
    
    def test_single_capture(self, start_x=None, end_x=None, start_y=None, end_y=None, debug=True):
        """
        Test function to capture and analyze a single image
        
        Args:
            start_x (int, optional): Starting x-coordinate for analysis region
            end_x (int, optional): Ending x-coordinate for analysis region
            start_y (int, optional): Starting y-coordinate for analysis region
            end_y (int, optional): Ending y-coordinate for analysis region
            debug (bool, optional): Whether to enable debug mode. Defaults to True.
        """
        print("🧪 Testing single image download...")
        
        # Test direct image download and save original in full color
        full_image = self.download_chart_image()
        if full_image is None:
            print("❌ Could not download image")
            print("\n🔧 Troubleshooting steps:")
            print("1. Check your internet connection")
            print("2. Try accessing https://stockcharts.com/c-sc/sc?s=%24NYSI manually in browser")
            print("3. StockCharts might be blocking automated requests")
            return
            
        # Save the original PIL image if in debug mode
        if debug:
            full_image.save("full_chart_orig.png")
            print("💾 Original image saved as: full_chart_orig.png")
        
        # Convert PIL image to OpenCV format
        full_image_opencv_rgb = np.array(full_image.convert('RGB'))
        full_image_opencv = cv2.cvtColor(full_image_opencv_rgb, cv2.COLOR_RGB2BGR)  # RGB to BGR for OpenCV
        
        print(f"✅ Successfully converted downloaded image to OpenCV format: shape={full_image_opencv.shape}")
        self.image_processor.save_debug_image(full_image_opencv, "full_chart_opencv.png", debug)
        
        # Get the actual start_x and end_x values that will be used
        height, width = full_image_opencv.shape[:2]
        
        # Calculate actual x coordinates
        if start_x is None or end_x is None:
            actual_start_x = min(self.image_processor.DEFAULT_START_X, width)
            actual_end_x = min(self.image_processor.DEFAULT_END_X, width)
        else:
            actual_start_x = max(0, min(start_x, width - 1))
            actual_end_x = max(start_x + 1, min(end_x, width))
        
        # Calculate actual y coordinates
        if start_y is None or end_y is None:
            actual_start_y = self.image_processor.DEFAULT_START_Y  # Default value
            actual_end_y = min(self.image_processor.DEFAULT_END_Y, height)  # Default value
        else:
            actual_start_y = max(0, min(start_y, height - 1))
            actual_end_y = max(start_y + 1, min(end_y, height))
        
        print(f"🔍 Analysis region: x={actual_start_x} to x={actual_end_x} (width: {actual_end_x-actual_start_x}px)")
        print(f"🔍 Analysis region: y={actual_start_y} to y={actual_end_y} (height: {actual_end_y-actual_start_y}px)")
        
        # Extract middle section
        middle_section = self.image_processor.extract_middle_section(full_image_opencv, start_x, end_x, start_y, end_y)
        if middle_section is None:
            print("❌ Could not extract middle section")
            return
            
        print(f"✅ Extracted middle section: {middle_section.shape}")
        self.image_processor.save_debug_image(middle_section, "middle_section_orig.png", debug)
        
        # Detect line crossing with detailed color data in debug mode
        crossing_result = self.image_processor.detect_line_crossing(middle_section, debug=True)
        
        # In debug mode, crossing_result is a tuple (result, color_data)
        crossing = crossing_result[0]
        color_data = crossing_result[1]
        
        # Get the rightmost column color from the color data
        rightmost_color = color_data.get("rightmost_color", "unknown")
        
        print(f"🔄 Line crossing detection: {crossing}")
        print(f"🔍 Rightmost column color: {rightmost_color} (used for logging when no change is detected)")
        
        # Explain the red line adjustment
        print("\n📝 Note: Red pixel counts are adjusted (-1 per column) to account for the fixed red line that runs through every column")
        print("   Raw pixel counts include this fixed line, while adjusted counts have it removed for more accurate analysis")
        print("   Red/Black Ratio > 1.0 indicates red is dominant, < 1.0 indicates black is dominant")
        
        # Print detailed column color information
        column_colors = color_data["column_colors"]
        column_data = color_data.get("column_data", [])
        print("\n📊 Column color analysis:")
        
        # Group consecutive colors for more readable output
        current_color = None
        start_col = 0
        
        for i, color in enumerate(column_colors):
            if color != current_color:
                if current_color is not None:
                    end_col = i - 1
                    if start_col == end_col:
                        print(f"  Column {start_col} (pixel {actual_start_x + start_col}): {current_color.upper()}")
                    else:
                        print(f"  Columns {start_col}-{end_col} (pixels {actual_start_x + start_col}-{actual_start_x + end_col}): {current_color.upper()}")
                current_color = color
                start_col = i
                
        # Handle the last segment
        if current_color is not None:
            end_col = len(column_colors) - 1
            if start_col == end_col:
                print(f"  Column {start_col} (pixel {actual_start_x + start_col}): {current_color.upper()}")
            else:
                print(f"  Columns {start_col}-{end_col} (pixels {actual_start_x + start_col}-{actual_start_x + end_col}): {current_color.upper()}")
        
        # Add detailed pixel count analysis
        if column_data:
            print("\n🔬 Detailed pixel analysis:")
            print("  Column | Red Pixels (Raw) | Red Pixels (Adj) | Black Pixels | Red/Black Ratio | Red % | Black %")
            print("  -------|-----------------|------------------|-------------|-----------------|-------|--------")
            
            # Display detailed info for each column
            for data in column_data:
                col = data["column"]
                ratio = data.get("red_to_black_ratio", 0)
                ratio_str = f"{ratio:.2f}" if ratio != float('inf') else "∞"
                print(f"  {col:6d} | {data.get('red_pixels_raw', data['red_pixels']):16d} | {data['red_pixels']:15d} | {data['black_pixels']:11d} | {ratio_str:15} | {data['red_percentage']:5d}% | {data['black_percentage']:6d}%")
            
            # Add a visual representation of the pixel distribution
            print("\n📈 Color distribution (R=Red, B=Black, -=Empty/Mixed):")
            print("  Column: " + "".join([f"{i%10}" for i in range(len(column_data))]))
            print("  Colors: ", end="")
            
            for data in column_data:
                red_pixels = data["red_pixels"]  # This is the adjusted count (fixed red line removed)
                black_pixels = data["black_pixels"]
                
                # Use the adjusted pixel counts directly for more accurate representation
                # This ensures consistency with the pixel analysis section
                if red_pixels > 0 and black_pixels > 0:
                    # Both colors present - which one is dominant?
                    if red_pixels >= black_pixels:
                        print("R", end="")
                    else:
                        print("B", end="")
                elif red_pixels > 0:
                    print("R", end="")
                elif black_pixels > 0:
                    print("B", end="")
                else:
                    print("-", end="")
            print()  # End the line
            
            # Add transition markers (need to access transitions before this point)
            transitions = color_data["transitions"]
            print("  Trans.: ", end="")
            for i in range(len(column_data)):
                if i in transitions["red_to_black"]:
                    print("↓", end="")  # Down arrow for red to black
                elif i in transitions["black_to_red"]:
                    print("↑", end="")  # Up arrow for black to red
                else:
                    print(" ", end="")
            print()  # End the line
        
        # Print transition information
        transitions = color_data["transitions"]
        print("\n🔀 Transition analysis:")
        
        if transitions["red_to_black"]:
            pixel_cols = [actual_start_x + col for col in transitions["red_to_black"]]
            print(f"  Red → Black transitions at columns: {transitions['red_to_black']} (pixels: {pixel_cols})")
            
            # Add detailed transition analysis if column_data is available
            if column_data:
                print("\n  Detailed Red → Black transitions:")
                print("  Column | Red Pixels Before (Adj) | Black Pixels After | Change %")
                print("  -------|-------------------------|-------------------|--------")
                for col in transitions["red_to_black"]:
                    if col > 0 and col < len(column_data):
                        before = column_data[col-1]
                        after = column_data[col]
                        change_pct = after["black_percentage"] - before["black_percentage"]
                        print(f"  {col:6d} | {before['red_pixels']:23d} | {after['black_pixels']:17d} | {change_pct:+6d}%")
        else:
            print("  No Red → Black transitions detected")
            
        if transitions["black_to_red"]:
            pixel_cols = [actual_start_x + col for col in transitions["black_to_red"]]
            print(f"  Black → Red transitions at columns: {transitions['black_to_red']} (pixels: {pixel_cols})")
            
            # Add detailed transition analysis if column_data is available
            if column_data:
                print("\n  Detailed Black → Red transitions:")
                print("  Column | Black Pixels Before | Red Pixels After (Adj) | Change %")
                print("  -------|---------------------|------------------------|--------")
                for col in transitions["black_to_red"]:
                    if col > 0 and col < len(column_data):
                        before = column_data[col-1]
                        after = column_data[col]
                        change_pct = after["red_percentage"] - before["red_percentage"]
                        print(f"  {col:6d} | {before['black_pixels']:19d} | {after['red_pixels']:22d} | {change_pct:+6d}%")
        else:
            print("  No Black → Red transitions detected")
        
        print("")  # Add empty line for readability
        
        # Show what would happen in stateful mode
        last_logged_crossing = self.notification_mgr.get_last_logged_crossing()
        print(f"🔍 Last logged crossing from log file: {last_logged_crossing}")
        
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Check if we should log this transition
        should_log = self.notification_mgr.should_log_transition(crossing)
        
        if crossing != "no_crossing" and should_log:
            print(f"✓ STATEFUL MODE: Would log and send email for {crossing} crossing")
            if last_logged_crossing == crossing:
                print(f"  (This is because it's a new day since the last time this crossing was logged)")
            else:
                print(f"  (This is because it's a different crossing type than last time)")
            print(f"  Log entry would be: [{today} HH:MM:SS] Line crossing detected: {crossing}")
        else:
            if crossing != "no_crossing":
                print(f"✗ STATEFUL MODE: Would NOT log/email (crossing {crossing} already logged today)")
                
                # If the current crossing matches the last logged crossing, show what the "no change" entry would look like
                if crossing == last_logged_crossing:
                    print(f"  Would log (without email): [{today} HH:MM:SS] Current line color: {rightmost_color}")
            else:
                print(f"✗ STATEFUL MODE: Would NOT log/email (no crossing detected)")
                print(f"  Would log (without email): [{today} HH:MM:SS] Current line color: {rightmost_color}")
                
        # If debug is enabled, show the bounding box of the analyzed area
        if debug:
            height, width = full_image_opencv.shape[:2]
            
            # Get the actual start_x, end_x, start_y, and end_y values used
            if start_x is None or end_x is None:
                start_x = min(self.image_processor.DEFAULT_START_X, width)
                end_x = min(self.image_processor.DEFAULT_END_X, width)
            
            if start_y is None or end_y is None:
                start_y = self.image_processor.DEFAULT_START_Y
                end_y = min(self.image_processor.DEFAULT_END_Y, height)
            
            # Draw rectangle around analyzed area - only 1 pixel thick
            box_image = full_image_opencv.copy()
            cv2.rectangle(box_image, (start_x, start_y), (end_x, end_y), (0, 255, 0), 1)
            
            # Define indicator dimensions
            indicator_height = 3  # 3 pixels high
            indicator_thickness = 1  # Make it 2 pixels thick for better visibility
            indicator_colour_red = (0, 0, 255)  # red in BGR
            indicator_colour_black = (0, 0, 0)
            
            # Add small indicators for transitions
            transitions = color_data["transitions"]
            
            # Mark red to black transitions
            for col in transitions["red_to_black"]:
                transition_x = start_x + col
                if 0 <= transition_x < width:
                    # Add indicator at the top of the box
                    cv2.line(box_image, (transition_x, start_y - indicator_height), 
                             (transition_x, start_y), indicator_colour_black, indicator_thickness)  # Red line above
                    # Add indicator at the bottom of the box
                    cv2.line(box_image, (transition_x, end_y), 
                             (transition_x, end_y + indicator_height), indicator_colour_black, indicator_thickness)  # Red line below
            
            # Mark black to red transitions
            for col in transitions["black_to_red"]:
                transition_x = start_x + col
                if 0 <= transition_x < width:
                    # Add indicator at the top of the box
                    cv2.line(box_image, (transition_x, start_y - indicator_height), 
                             (transition_x, start_y), indicator_colour_red, indicator_thickness)  # Blue line above (appears as blue in BGR)
                    # Add indicator at the bottom of the box
                    cv2.line(box_image, (transition_x, end_y), 
                             (transition_x, end_y + indicator_height), indicator_colour_red, indicator_thickness)  # Blue line below
            
            # Save the image with box
            self.image_processor.save_debug_image(box_image, "analysis_region.png", debug)
            print("💾 Analysis region visualization saved as: analysis_region.png")
    
    def monitor_line_crossings(self, check_interval=60, monitoring_window=True):
        """
        Monitor the chart for line crossings
        
        Parameters:
        - check_interval: Time between checks in seconds (default: 60)
        - monitoring_window: Whether to only monitor during specific hours (default: True)
                             If True, will monitor between MONITORING_START_HOUR:MONITORING_START_MINUTE and 
                             MONITORING_END_HOUR:MONITORING_END_MINUTE in the TIMEZONE timezone
                             If False, will monitor continuously
        """
        # Gather information for the comprehensive log entry
        from datetime import datetime, time as dt_time
        import glob
        
        # Get most recent chart image info
        most_recent_chart = None
        charts_dir = "downloaded_charts"
        clean_symbol = self.symbol.replace('$', '')
        pattern = os.path.join(charts_dir, f"stockcharts_{clean_symbol}_*.png")
        image_files = glob.glob(pattern)
        if image_files:
            # Sort files by date in filename (descending)
            def extract_date(f):
                m = re.search(r"_(\d{4}-\d{2}-\d{2})\.png$", f)
                if m:
                    try:
                        return datetime.strptime(m.group(1), "%Y-%m-%d")
                    except Exception:
                        return datetime.min
                return datetime.min
            image_files.sort(key=extract_date, reverse=True)
            most_recent_chart = image_files[0]
        
        # Check for most recent color in the log file
        last_color = None
        last_color_date = None
        log_file = self.notification_mgr.log_file
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                    
                for line in reversed(lines):
                    if "Current line color:" in line:
                        color_match = re.search(r"Current line color: (red|black)", line)
                        date_match = re.search(r"^\[([\d-]+)", line)
                        
                        if color_match and date_match:
                            last_color = color_match.group(1)
                            last_color_date = date_match.group(1)
                            break
            except Exception:
                pass
        
        # Get timezone information
        aest_timezone = pytz.timezone(self.TIMEZONE)
        timezone_abbr = aest_timezone.localize(datetime.now()).strftime('%Z')
        
        # Build the comprehensive start message
        start_message = f"Monitoring started for {self.symbol} with {check_interval} second interval"
        
        if most_recent_chart:
            start_message += f"\nMost recent chart image: {most_recent_chart}"
            
        if last_color and last_color_date:
            start_message += f"\nMost recent detected color: {last_color.upper()} (detected on {last_color_date})"
        
        if monitoring_window:
            start_message += f"\nMonitoring window: {self.MONITORING_START_HOUR}:{self.MONITORING_START_MINUTE:02d} AM - {self.MONITORING_END_HOUR}:{self.MONITORING_END_MINUTE:02d} AM {timezone_abbr} ({self.TIMEZONE})"
        else:
            start_message += "\nMonitoring continuously (24/7)"
            
        # Log the comprehensive start message
        self.notification_mgr.log_transition(start_message, "monitoring_started", send_email=False)

        # Check for a recent saved chart in downloaded_charts and log it
        date_str = datetime.now().strftime('%Y-%m-%d')
        filename = f"stockcharts_{clean_symbol}_{date_str}.png"
        filepath = os.path.join(charts_dir, filename)
        if os.path.exists(filepath):
            log_msg = f"Recent saved chart detected: {filename}"
            self.notification_mgr.log_transition(log_msg, "recent_saved_chart", send_email=False)

        # Check if log file exists and show the most recent color
        if os.path.exists(log_file):
            print(f"📊 Existing log file detected: {log_file}")
            try:
                last_color = None
                last_color_date = None
                
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                    
                    for line in reversed(lines):
                        if "Current line color:" in line:
                            color_match = re.search(r"Current line color: (red|black)", line)
                            date_match = re.search(r"^\[([\d-]+)", line)
                            
                            if color_match and date_match:
                                last_color = color_match.group(1)
                                last_color_date = date_match.group(1)
                                break
                
                if last_color and last_color_date:
                    print(f"🔍 Most recent detected color: {last_color.upper()} (detected on {last_color_date})")
                else:
                    print("🔍 No previous color detection found in log file")
            except Exception as e:
                print(f"⚠️ Error reading log file: {e}")
        else:
            print(f"📊 No existing log file found. Will create {log_file} when first transition is detected")
        
        try:
            # Import time separately to avoid naming conflict with datetime module
            from datetime import time as dt_time
            
            # Set up timezone using class constant
            aest_timezone = pytz.timezone(self.TIMEZONE)
            
            # Get timezone abbreviation for display
            timezone_abbr = aest_timezone.localize(datetime.now()).strftime('%Z')
            
            if monitoring_window:
                print(f"Will check between {self.MONITORING_START_HOUR}:{self.MONITORING_START_MINUTE:02d} AM and {self.MONITORING_END_HOUR}:{self.MONITORING_END_MINUTE:02d} AM {timezone_abbr} only")
                print("Outside the monitoring window: No output will be shown until the window starts")
            else:
                print("Monitoring continuously (no time window restrictions)")
                
            print("Press Ctrl+C to stop monitoring")
            
            # Set up timezone using class constant
            aest_timezone = pytz.timezone(self.TIMEZONE)
            
            # Define time window for monitoring using class constants
            start_time = dt_time(self.MONITORING_START_HOUR, self.MONITORING_START_MINUTE)
            end_time = dt_time(self.MONITORING_END_HOUR, self.MONITORING_END_MINUTE)
            
            # Track if we were in the monitoring window in the previous iteration
            was_in_window = False
            
            while True:
                # Get current time in AEST
                now = datetime.now(aest_timezone)
                current_time = now.time()
                
                # Format for display
                time_str = now.strftime('%Y-%m-%d %H:%M:%S %Z')
                
                # Check if current time is within the monitoring window or if window check is disabled
                in_window = not monitoring_window or (start_time <= current_time <= end_time)
                
                # Log entering the monitoring window
                if in_window and not was_in_window and monitoring_window:
                    message = f"Entering monitoring window at {time_str}"
                    print(f"\n--- {message} ---")
                    self.notification_mgr.log_transition(message, "enter_window", send_email=False)
                
                # Log exiting the monitoring window
                if not in_window and was_in_window and monitoring_window:
                    message = f"Exiting monitoring window at {time_str}"
                    print(f"\n--- {message} ---")
                    self.notification_mgr.log_transition(message, "exit_window", send_email=False)
                    print("Outside monitoring window: No output will be shown until the next window starts")
                
                # Update window state for next iteration
                was_in_window = in_window
                
                if in_window:
                    print(f"\n--- Check at {time_str} ---")
                    
                    # Download image (this will get fresh URL each time)
                    full_image = self.download_chart_image()
                    
                    if full_image is not None:
                        # Convert PIL image to OpenCV format
                        full_image_opencv_rgb = np.array(full_image.convert('RGB'))
                        full_image_opencv = cv2.cvtColor(full_image_opencv_rgb, cv2.COLOR_RGB2BGR)
                        
                        # Check if the image is different from the previously saved one
                        # Log that we're comparing the downloaded image with the existing one
                        existing_image_info = f"Comparing newly downloaded image with existing image"
                        if self.last_saved_image_path:
                            # Extract just the filename from the path
                            existing_filename = os.path.basename(self.last_saved_image_path)
                            existing_image_info += f" ({existing_filename})"
                        self.notification_mgr.log_transition(existing_image_info, "image_comparison", send_email=False)
                        if self.image_processor.images_are_different(full_image_opencv, self.last_saved_image):
                            print("💾 New chart image detected - saving...")
                            self.notification_mgr.log_transition("New image detected", "image_comparison", send_email=False)
                            filepath = self.save_chart_image(full_image)
                            # Update the last saved image
                            self.last_saved_image = full_image_opencv
                            if filepath:
                                self.last_saved_image_path = filepath
                            
                            # Only analyze the image if it's new
                            # Extract middle section
                            middle_section = self.image_processor.extract_middle_section(full_image_opencv)
                            
                            # Detect line crossing
                            crossing_result = self.image_processor.detect_line_crossing(middle_section, debug=False)
                            # Since debug is False, crossing_result is a tuple (crossing_type, rightmost_color)
                            current_crossing, rightmost_color = crossing_result
                            
                            # Get the last logged crossing from the log file
                            last_crossing = self.notification_mgr.get_last_logged_crossing()
                            
                            # Get the last color from the logs to detect color changes even when the crossing detection doesn't pick it up
                            last_color = None
                            try:
                                with open(self.notification_mgr.log_file, 'r') as f:
                                    lines = f.readlines()
                                    for line in reversed(lines):
                                        if "Current line color:" in line:
                                            color_match = re.search(r"Current line color: (red|black)", line)
                                            if color_match:
                                                last_color = color_match.group(1)
                                                break
                            except Exception as e:
                                print(f"⚠️ Error reading last color from log file: {e}")
                            
                            # Detect a color change that might not have been detected as a crossing
                            manual_color_crossing = "no_crossing"
                            if last_color and last_color != rightmost_color:
                                if last_color == "red" and rightmost_color == "black":
                                    manual_color_crossing = "red_to_black"
                                elif last_color == "black" and rightmost_color == "red":
                                    manual_color_crossing = "black_to_red"
                            
                            # Use the detected crossing or manual color crossing if detected
                            effective_crossing = current_crossing
                            if current_crossing == "no_crossing" and manual_color_crossing != "no_crossing":
                                effective_crossing = manual_color_crossing
                            
                            # Check if we should log this transition based on log file history
                            if effective_crossing != "no_crossing" and self.notification_mgr.should_log_transition(effective_crossing):
                                message = f"⚠️ Line crossing detected: {effective_crossing}"
                                print(message)
                                # Log the crossing and send email notification
                                self.notification_mgr.log_transition(f"Line crossing detected: {effective_crossing}", effective_crossing)
                                # Add the current line color as a new line in the logs (silently)
                                self.notification_mgr.log_transition(f"Current line color: {rightmost_color}", f"{effective_crossing}_color", send_email=False, silent=True)
                            else:
                                # Just report the current state without logging or emailing
                                if effective_crossing != "no_crossing":
                                    print(f"📊 Line crossing remains: {effective_crossing}")
                                    
                                    # If the current crossing matches the last logged crossing, add a "no change" entry
                                    if effective_crossing == last_crossing:
                                        # Log the rightmost column color, but don't send an email notification
                                        self.notification_mgr.log_transition(f"Current line color: {rightmost_color}", f"{effective_crossing}_unchanged", send_email=False)
                                else:
                                    print(f"📊 No line crossing detected, current color: {rightmost_color}")
                                    # Log the rightmost column color without sending an email
                                    self.notification_mgr.log_transition(f"Current line color: {rightmost_color}", "no_crossing", send_email=False)
                        else:
                            print("📊 Chart image unchanged since last check")
                            # Log that the image is unchanged
                            self.notification_mgr.log_transition("Chart image unchanged since last check", "unchanged_image", send_email=False)
                            # Skip further analysis since the image is unchanged
                    else:
                        print("❌ Failed to download image")
                        # Log the failed download
                        self.notification_mgr.log_transition("No new image available - download failed", "image_download_failed", send_email=False)
                # When outside the monitoring window, just sleep without output
                # Only calculate time to next window for internal use (no output)
                elif monitoring_window:
                    if current_time < start_time:
                        # Calculate time until monitoring starts but don't print
                        start_dt = datetime.combine(now.date(), start_time)
                        start_dt = aest_timezone.localize(start_dt)
                    else:  # current_time > end_time
                        # Calculate time until next day's window but don't print
                        next_start_dt = datetime.combine(now.date(), start_time)
                        next_start_dt = aest_timezone.localize(next_start_dt)
                        if next_start_dt <= now:  # If start time is tomorrow
                            next_start_dt = next_start_dt.replace(day=next_start_dt.day+1)
                
                # Wait before next check
                time.sleep(check_interval)
                
        except KeyboardInterrupt:
            # Log that monitoring was stopped by the user
            stop_message = "Monitoring stopped by user"
            print(f"\n🛑 {stop_message}")
            self.notification_mgr.log_transition(stop_message, "monitoring_stopped", send_email=False)
        except Exception as e:
            # Log that monitoring stopped due to an error
            error_message = f"Monitoring stopped due to error: {e}"
            print(f"💥 {error_message}")
            self.notification_mgr.log_transition(error_message, "monitoring_error", send_email=False)


def parse_args():
    """Parse command-line arguments using argparse"""
    parser = argparse.ArgumentParser(
        description="Stock Chart Color Monitor - Track red/black line transitions",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Test command
    test_parser = subparsers.add_parser(
        "test", 
        help="Run a single test capture and analysis",
        description="Run a single test capture and analysis of the stock chart"
    )
    test_parser.add_argument("--symbol", default="$NYSI", help="Stock symbol to monitor (default: $NYSI)")
    test_parser.add_argument("--x-region", nargs=2, type=int, metavar=("START_X", "END_X"),
                       help="Horizontal region to analyze (x coordinates)")
    test_parser.add_argument("--y-region", nargs=2, type=int, metavar=("START_Y", "END_Y"),
                       help="Vertical region to analyze (y coordinates)")
    test_parser.add_argument("--no-debug", action="store_true", help="Disable debug mode")
    
    # Monitor command
    monitor_parser = subparsers.add_parser(
        "monitor", 
        help="Start continuous monitoring",
        description="Start continuous monitoring of the stock chart for line crossings"
    )
    monitor_parser.add_argument("--symbol", default="$NYSI", help="Stock symbol to monitor (default: $NYSI)")
    monitor_parser.add_argument("--interval", type=int, default=30,
                          help="Interval between checks in seconds (default: 30)")
    # Use timezone abbreviation in the help text
    timezone_abbr = pytz.timezone(StockChartMonitor.TIMEZONE).localize(datetime.now()).strftime('%Z')
    monitor_parser.add_argument("--continuous", action="store_true",
                          help=f"Run continuously regardless of time (default: only checks during {StockChartMonitor.MONITORING_START_HOUR}:{StockChartMonitor.MONITORING_START_MINUTE:02d}-{StockChartMonitor.MONITORING_END_HOUR}:{StockChartMonitor.MONITORING_END_MINUTE:02d} AM {timezone_abbr})")
    
    return parser.parse_args()


def main():
    """Main entry point"""
    # Parse command-line arguments
    args = parse_args()
    
    if args.command == "test":
        print("Running single capture test...")
        monitor = StockChartMonitor(args.symbol)
        
        # Extract region coordinates
        start_x = None
        end_x = None
        start_y = None
        end_y = None
        
        if args.x_region:
            start_x, end_x = args.x_region
            print(f"Using custom analysis region: x={start_x} to x={end_x}")
            
        if args.y_region:
            start_y, end_y = args.y_region
            print(f"Using custom analysis region: y={start_y} to y={end_y}")
            
        # Run the test
        monitor.test_single_capture(start_x, end_x, start_y, end_y, debug=not args.no_debug)
    
    elif args.command == "monitor":
        print(f"Starting to monitor {args.symbol} with {args.interval} second interval...")
        
        if args.continuous:
            print("Running in continuous monitoring mode (no time window restrictions)")
        
        monitor = StockChartMonitor(args.symbol)
        monitor.monitor_line_crossings(check_interval=args.interval, monitoring_window=not args.continuous)
    
    else:
        # If no command is specified, show usage
        print("Stock Chart Color Monitor")
        print("Usage:")
        print("  python stock_monitor.py test [--symbol SYMBOL] [--x-region START_X END_X] [--y-region START_Y END_Y] [--no-debug]")
        print("  python stock_monitor.py monitor [--symbol SYMBOL] [--interval SECONDS] [--continuous]")
        print("\nExamples:")
        print("  python stock_monitor.py test")
        print("  python stock_monitor.py test --x-region 600 640")
        print("  python stock_monitor.py test --x-region 600 640 --y-region 200 300")
        print("  python stock_monitor.py monitor")
        print("  python stock_monitor.py monitor --interval 60")
        print("  python stock_monitor.py monitor --interval 60 --continuous")
        print("\nMonitoring Modes:")
        timezone_abbr = pytz.timezone(StockChartMonitor.TIMEZONE).localize(datetime.now()).strftime('%Z')
        print(f"  Default mode: Only checks during {StockChartMonitor.MONITORING_START_HOUR}:{StockChartMonitor.MONITORING_START_MINUTE:02d}-{StockChartMonitor.MONITORING_END_HOUR}:{StockChartMonitor.MONITORING_END_MINUTE:02d} AM {timezone_abbr} ({StockChartMonitor.TIMEZONE})")
        print("  Continuous mode: Checks 24/7 regardless of time")
        
        print("\nEmail Notifications:")
        print("  To enable email notifications, update email_details.py with your credentials:")
        print("    sender_email = \"your.email@gmail.com\"")
        print("    sender_password_password = \"your-app-password\"")
        print("    recipients = [\"recipient1@example.com\", \"recipient2@example.com\"]")
        print("\nLogging:")
        print("  All transitions and current colors will be logged to a text file (logs/SYMBOL_changes.log) with timestamps")
        print("  Color transitions trigger email notifications, current color updates don't")


if __name__ == "__main__":
    main()
