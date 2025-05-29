import requests
import cv2
import numpy as np
from PIL import Image
from io import BytesIO
import time
import re
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

class StockChartMonitor:
    def __init__(self, symbol="$NYSI"):
        self.symbol = symbol
        self.base_url = f"https://stockcharts.com/sc3/ui/?s={symbol}"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
    
    def get_current_chart_url(self):
        """Generate the current chart URL using StockCharts' direct API"""
        import random
        
        # Generate parameters similar to what StockCharts uses
        random_id = random.randint(1000000000, 9999999999)  # 10-digit random number
        timestamp = int(time.time() * 1000)  # Current timestamp in milliseconds
        
        chart_url = f"https://stockcharts.com/c-sc/sc?s=%24NYSI&p=D&yr=1&mn=0&dy=0&i=t{random_id}c&r={timestamp}"
        
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
    
    def extract_middle_section(self, image, start_x=None, end_x=None):
        """Extract the middle section of the chart where the line is located"""
        if image is None:
            return None
        
        height, width = image.shape[:2]
        
        # Define middle section with specific pixel measurements
        start_y = 125
        end_y = 403
        
        # Use provided horizontal bounds if specified, otherwise use defaults
        if start_x is None or end_x is None:
            # Default: analyze pixels from 620 to 650
            start_x = min(620, width)
            end_x = min(650, width)
        else:
            # Ensure we don't exceed image dimensions
            start_x = max(0, min(start_x, width - 1))
            end_x = max(start_x + 1, min(end_x, width))
        
        # Ensure we don't go out of bounds
        end_y = min(end_y, height)
        
        middle_section = image[start_y:end_y, start_x:end_x]
        return middle_section
    
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
        red_lower1 = np.array([0, 30, 30])
        red_upper1 = np.array([15, 255, 255])
        red_lower2 = np.array([165, 30, 30])
        red_upper2 = np.array([180, 255, 255])
        
        black_lower = np.array([0, 0, 0])
        black_upper = np.array([180, 100, 80])
        
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
                    "transitions": {
                        "red_to_black": [],
                        "black_to_red": []
                    }
                }
            return "no_crossing"
        
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
    
    def log_transition(self, message, crossing_type, send_email=True):
        """
        Log a transition event to a file with timestamp and send email notification
        
        Args:
            message (str): The message to log
            crossing_type (str): The type of crossing detected (e.g., 'red_to_black', 'black_to_red')
            send_email (bool): Whether to send an email notification (default: True)
        """
        log_file = "nysi_changes.txt"
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Remove emojis and other special characters from message for log file
        clean_message = re.sub(r'[^\x00-\x7F]+', '', message)
        
        # Format the log entry
        log_entry = f"[{timestamp}] {clean_message}"
        
        # Write to log file
        with open(log_file, 'a') as f:
            f.write(log_entry + '\n')
        
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
            subject = f"NYSI Alert: {crossing_type.replace('_', ' ').title()} Transition Detected"
            
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
                print(f"📧 Email notification sent to {', '.join(recipients)}")
            except Exception as e:
                print(f"❌ Failed to send email notification: {e}")
                
        except ImportError as e:
            print(f"⚠️ Error importing email configuration: {e}")
            print("Please make sure email_details.py is properly configured")
        except Exception as e:
            print(f"⚠️ Error in email configuration: {e}")
    
    def monitor_line_crossings(self, check_interval=60, monitoring_window=True):
        """
        Monitor the chart for line crossings
        
        Parameters:
        - check_interval: Time between checks in seconds (default: 60)
        - monitoring_window: Whether to only monitor during specific hours (default: True)
                             If False, will monitor continuously
        """
        print(f"Starting to monitor {self.symbol} for line crossings")
        print(f"Checking every {check_interval} seconds")
        
        if monitoring_window:
            print(f"Will check between 9:30 AM and 10:00 AM AEST only")
        else:
            print("Monitoring continuously (no time window restrictions)")
            
        print("Press Ctrl+C to stop monitoring")
        
        try:
            import pytz
            from datetime import datetime, time as dt_time
            
            # Set up timezone
            aest_timezone = pytz.timezone('Australia/Sydney')
            
            # Define time window for monitoring (9:30 AM to 10:00 AM AEST)
            start_time = dt_time(9, 30)  # 9:30 AM
            end_time = dt_time(10, 0)    # 10:00 AM
            
            while True:
                # Get current time in AEST
                now = datetime.now(aest_timezone)
                current_time = now.time()
                
                # Format for display
                time_str = now.strftime('%Y-%m-%d %H:%M:%S %Z')
                
                # Check if current time is within the monitoring window or if window check is disabled
                if not monitoring_window or (start_time <= current_time <= end_time):
                    print(f"\n--- Check at {time_str} ---")
                    
                    # Download image (this will get fresh URL each time)
                    full_image = self.download_chart_image()
                    
                    if full_image is not None:
                        # Convert PIL image to OpenCV format
                        full_image_opencv_rgb = np.array(full_image.convert('RGB'))
                        full_image_opencv = cv2.cvtColor(full_image_opencv_rgb, cv2.COLOR_RGB2BGR)
                        
                        # Extract middle section
                        middle_section = self.extract_middle_section(full_image_opencv)
                        
                        # Detect line crossing
                        crossing_result = self.detect_line_crossing(middle_section, debug=False)
                        # Since debug is False, crossing_result is a tuple (crossing_type, rightmost_color)
                        current_crossing, rightmost_color = crossing_result
                        
                        # Get the last logged crossing from the log file
                        last_crossing = self.get_last_logged_crossing()
                        
                        # Check if we should log this transition based on log file history
                        if current_crossing != "no_crossing" and self.should_log_transition(current_crossing):
                            message = f"⚠️ Line crossing detected: {current_crossing}"
                            print(message)
                            # Log the crossing and send email notification
                            self.log_transition(f"Line crossing detected: {current_crossing}", current_crossing)
                        else:
                            # Just report the current state without logging or emailing
                            if current_crossing != "no_crossing":
                                print(f"📊 Line crossing remains: {current_crossing}")
                                
                                # If the current crossing matches the last logged crossing, add a "no change" entry
                                if current_crossing == last_crossing:
                                    # Log the rightmost column color, but don't send an email notification
                                    self.log_transition(f"Current line color: {rightmost_color}", f"{current_crossing}_unchanged", send_email=False)
                            else:
                                print(f"📊 No line crossing detected, current color: {rightmost_color}")
                                # Log the rightmost column color without sending an email
                                self.log_transition(f"Current line color: {rightmost_color}", "no_crossing", send_email=False)
                    else:
                        print("❌ Failed to download image")
                elif monitoring_window:
                    # Only print window messages if window checking is enabled
                    # Calculate minutes until next monitoring window
                    minutes_to_start = None
                    if current_time < start_time:
                        # Calculate time until monitoring starts
                        start_dt = datetime.combine(now.date(), start_time)
                        start_dt = aest_timezone.localize(start_dt)
                        delta = start_dt - now
                        minutes_to_start = int(delta.total_seconds() / 60)
                        print(f"Outside monitoring window - Current time: {time_str}")
                        print(f"Monitoring will start in approximately {minutes_to_start} minutes")
                    else:  # current_time > end_time
                        # Calculate time until next monitoring window
                        next_start_dt = datetime.combine(now.date(), start_time)
                        next_start_dt = aest_timezone.localize(next_start_dt)
                        if next_start_dt <= now:  # If start time is tomorrow
                            next_start_dt = next_start_dt.replace(day=next_start_dt.day+1)
                        delta = next_start_dt - now
                        minutes_to_start = int(delta.total_seconds() / 60)
                        print(f"Outside monitoring window - Current time: {time_str}")
                        print(f"Monitoring window has ended for today. Next window in approximately {minutes_to_start} minutes")
                
                # Wait before next check
                time.sleep(check_interval)
                
        except KeyboardInterrupt:
            print("\n🛑 Monitoring stopped by user")
        except Exception as e:
            print(f"💥 Error during monitoring: {e}")
    
    def test_single_capture(self, start_x=None, end_x=None, debug=True):
        """
        Test function to capture and analyze a single image
        """
        print("🧪 Testing single image download...")
        
        # Test direct image download and save original in full color
        full_image = self.download_chart_image()
        if full_image is None:
            print("❌ Could not download image")
            print("\n🔧 Troubleshooting steps:")
            print("1. Check your internet connection")
            print("2. Try accessing https://stockcharts.com/c-sc/sc?s=%24NYSI&p=D manually in browser")
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
        self.save_debug_image(full_image_opencv, "full_chart_opencv.png", debug)
        
        # Get the actual start_x and end_x values that will be used
        height, width = full_image_opencv.shape[:2]
        
        # Calculate actual x coordinates
        if start_x is None or end_x is None:
            actual_start_x = min(620, width)
            actual_end_x = min(652, width)
        else:
            actual_start_x = max(0, min(start_x, width - 1))
            actual_end_x = max(start_x + 1, min(end_x, width))
        
        print(f"🔍 Analysis region: x={actual_start_x} to x={actual_end_x} (width: {actual_end_x-actual_start_x}px)")
        
        # Extract middle section
        middle_section = self.extract_middle_section(full_image_opencv, start_x, end_x)
        if middle_section is None:
            print("❌ Could not extract middle section")
            return
            
        print(f"✅ Extracted middle section: {middle_section.shape}")
        self.save_debug_image(middle_section, "middle_section_orig.png", debug)
        
        # Detect line crossing with detailed color data in debug mode
        crossing_result = self.detect_line_crossing(middle_section, debug=True)
        
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
        last_logged_crossing = self.get_last_logged_crossing()
        print(f"🔍 Last logged crossing from log file: {last_logged_crossing}")
        
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Check if we should log this transition
        should_log = self.should_log_transition(crossing)
        
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
            start_y = 125
            end_y = min(403, height)
            
            # Get the actual start_x and end_x values used
            if start_x is None or end_x is None:
                start_x = min(620, width)
                end_x = min(652, width)
            
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
                        
            self.save_debug_image(box_image, "analysis_region.png", debug)
            print("💾 Image with analysis region marked saved as: analysis_region.png")
    
    def get_last_logged_crossing(self):
        """
        Read the log file and extract the most recent crossing type.
        Returns "no_crossing" if the log file doesn't exist or no crossing is found.
        """
        log_file = "nysi_changes.txt"
        
        # Check if log file exists
        if not os.path.exists(log_file):
            return "no_crossing"
            
        try:
            # Read the last line of the log file
            with open(log_file, 'r') as f:
                lines = f.readlines()
                
            if not lines:
                return "no_crossing"
                
            # Find the last line with a crossing (ignoring "unchanged" entries)
            for line in reversed(lines):
                # Extract crossing type from the line
                if "red_to_black" in line and "unchanged" not in line:
                    return "red_to_black"
                elif "black_to_red" in line and "unchanged" not in line:
                    return "black_to_red"
                    
            # If we didn't find a definitive crossing, check the last line
            last_line = lines[-1].strip()
            
            # Extract base crossing type even if it's an "unchanged" entry
            if "red_to_black" in last_line:
                return "red_to_black"
            elif "black_to_red" in last_line:
                return "black_to_red"
            else:
                return "no_crossing"
                
        except Exception as e:
            print(f"⚠️ Error reading log file: {e}")
            return "no_crossing"
            
    def should_log_transition(self, current_crossing):
        """
        Determine if a transition should be logged based on the log file history.
        Only log if it's a new crossing type or it's the first time seeing this crossing today.
        """
        if current_crossing == "no_crossing" or "_unchanged" in current_crossing:
            return False
            
        # Get the last logged crossing
        last_crossing = self.get_last_logged_crossing()
        
        # If we've never logged this crossing type before, log it
        if last_crossing != current_crossing:
            return True
            
        # Check if we've already logged this crossing type today
        log_file = "nysi_changes.txt"
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Check if log file exists
        if not os.path.exists(log_file):
            return True
            
        try:
            # Read the log file
            with open(log_file, 'r') as f:
                lines = f.readlines()
                
            # Reverse the lines to start from the most recent
            for line in reversed(lines):
                if current_crossing in line and "_unchanged" not in line:
                    # Extract the date from the log entry [YYYY-MM-DD HH:MM:SS]
                    log_date_match = re.search(r'\[([\d]{4}-[\d]{2}-[\d]{2})', line)
                    if not log_date_match:
                        continue
                        
                    log_date = log_date_match.group(1)
                    
                    # If the log date is today, we've already logged this crossing today
                    if log_date == today:
                        return False
                    else:
                        # It's a new day, log it
                        return True
                     # If we didn't find any previous entries with this crossing, log it
            return True
                
        except Exception as e:
            print(f"⚠️ Error checking log file: {e}")
            # If there's an error, be safe and log it
            return True
    
    def get_rightmost_column_color(self, column_colors):
        """
        Get the color of the rightmost column in the analysis area
        Returns "red", "black", or "unknown"
        """
        if not column_colors or len(column_colors) == 0:
            return "unknown"
            
        # Return the color of the last column in the list
        return column_colors[-1]


if __name__ == "__main__":
    import sys
    
    # Check for command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            print("Running single capture test...")
            monitor = StockChartMonitor("$NYSI")
            
            # Check if custom analysis region is specified
            start_x = None
            end_x = None
            
            if len(sys.argv) > 3:
                try:
                    start_x = int(sys.argv[2])
                    end_x = int(sys.argv[3])
                    print(f"Using custom analysis region: x={start_x} to x={end_x}")
                except ValueError:
                    print("Invalid region coordinates, using defaults")
            
            monitor.test_single_capture(start_x, end_x)
            
        elif sys.argv[1] == "monitor":
            interval = 30
            continuous = False
            
            # Check for interval parameter
            if len(sys.argv) > 2:
                try:
                    interval = int(sys.argv[2])
                except ValueError:
                    print("Invalid interval, using default 30 seconds")
            
            # Check for continuous monitoring flag
            if len(sys.argv) > 3 and sys.argv[3].lower() in ["true", "continuous", "1", "yes"]:
                continuous = True
                print("Running in continuous monitoring mode (no time window restrictions)")
            
            monitor = StockChartMonitor("$NYSI")
            monitor.monitor_line_crossings(check_interval=interval, monitoring_window=not continuous)
        else:
            print("Usage:")
            print("  python stock_monitor.py test [start_x end_x]          # Run single capture test with optional analysis region")
            print("  python stock_monitor.py monitor [interval] [continuous]  # Start monitoring (optional interval in seconds)")
    else:
        # Default behavior - show usage
        print("Stock Chart Color Monitor")
        print("Usage:")
        print("  python stock_monitor.py test [start_x end_x]                # Run single capture test with optional analysis region")
        print("  python stock_monitor.py monitor [interval] [continuous]     # Start monitoring (optional interval in seconds)")
        print("\nExamples:")
        print("  python stock_monitor.py test")
        print("  python stock_monitor.py test 600 640                      # Analyze pixels 600-640 horizontally")
        print("  python stock_monitor.py monitor                           # Check every 30 seconds during 9:30-10:00 AM AEST")
        print("  python stock_monitor.py monitor 60                        # Check every 60 seconds during 9:30-10:00 AM AEST")
        print("  python stock_monitor.py monitor 60 continuous             # Check every 60 seconds, 24/7")
        print("\nMonitoring Modes:")
        print("  Default mode: Only checks during 9:30-10:00 AM AEST (Australian Eastern Standard Time)")
        print("  Continuous mode: Checks 24/7 regardless of time")
        print("  Add 'continuous' to the monitor command to enable continuous monitoring")
        
        print("\nEmail Notifications:")
        print("  To enable email notifications, update email_details.py with your credentials:")
        print("    sender_email = \"your.email@gmail.com\"")
        print("    sender_password_password = \"your-app-password\"")
        print("    recipients = [\"recipient1@example.com\", \"recipient2@example.com\"]")
        print("\nLogging:")
        print("  All transitions and current colors will be logged to nysi_changes.txt with timestamps")
        print("  Color transitions trigger email notifications, current color updates don't")
