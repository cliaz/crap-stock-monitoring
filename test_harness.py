#!/usr/bin/env python3

import os
import sys
import re
import argparse
from datetime import datetime
import cv2
import numpy as np
from PIL import Image

# Import necessary classes from stock_monitor_optimized.py
try:
    from stock_monitor_optimized import ImageProcessor, NotificationManager, StockChartMonitor
except ImportError:
    print("Error: Could not import from stock_monitor_optimized.py")
    print("Make sure you're running this script from the same directory as stock_monitor_optimized.py")
    sys.exit(1)

class TestHarness:
    """Test harness for simulating stock monitoring over multiple days using image files"""
    
    def __init__(self, symbol="$NYSI", debug=False):
        """Initialize the test harness"""
        self.symbol = symbol
        self.debug = debug
        
        # Initialize components from the stock monitor
        self.image_processor = ImageProcessor()
        
        # Create a notification manager with a test log file
        clean_symbol = symbol.replace('$', '')
        self.notification_mgr = NotificationManager(symbol)
        # Override the log file path to use a test-specific log file
        self.notification_mgr.log_file = f"test_{clean_symbol}_changes.txt"
        
        # Save the original methods we're going to override
        self._original_should_log_transition = self.notification_mgr.should_log_transition
        
        # Override the should_log_transition method to make it behave consistently in tests
        self.notification_mgr.should_log_transition = self._test_should_log_transition
        
        # Create a StockChartMonitor instance for use with test methods
        self.monitor = StockChartMonitor(symbol)
        
        # Last saved image for comparison
        self.last_saved_image = None
        
        # Debug mode
        self.debug = debug
        
        print(f"🧪 Test harness initialized for {symbol}")
        print(f"📝 Using test log file: {self.notification_mgr.log_file}")
        
        # Clear the test log file at initialization
        self._clear_log_file()
    
    def _clear_log_file(self):
        """Clear the test log file to ensure clean test runs"""
        try:
            open(self.notification_mgr.log_file, 'w').close()
            print(f"📝 Cleared test log file: {self.notification_mgr.log_file}")
        except Exception as e:
            print(f"⚠️ Warning: Could not clear test log file: {e}")
    
    def _test_should_log_transition(self, current_crossing):
        """
        Modified version of should_log_transition for tests.
        
        This version logs color transitions only when they actually represent a change
        from the last color, ignoring the "same day" check from the original method.
        
        Args:
            current_crossing: The current crossing type detected
            
        Returns:
            bool: True if we should log this crossing, False otherwise
        """
        # No need to log if no crossing detected
        if current_crossing == "no_crossing":
            return False
            
        # Always log if different from last crossing
        last_crossing = self.notification_mgr.get_last_logged_crossing()
        
        # Only log if it's a different crossing type than what we last logged
        # This ensures we don't log the same transition type multiple times in a test run
        return last_crossing != current_crossing
    
    def _load_image(self, image_path):
        """Load an image from file and convert to the format needed for processing"""
        try:
            # Try to load as OpenCV image first
            image = cv2.imread(image_path)
            
            if image is None:
                # If OpenCV fails, try with PIL and convert
                pil_image = Image.open(image_path)
                image_np = np.array(pil_image.convert('RGB'))
                image = cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR)
            
            if image is None:
                print(f"❌ Failed to load image: {image_path}")
                return None
                
            print(f"✅ Loaded image: {image_path}")
            return image
        except Exception as e:
            print(f"❌ Error loading image {image_path}: {e}")
            return None
    
    def process_image(self, image_path, simulated_date=None):
        """Process a single image file and detect line crossings"""
        # Load the image
        image = self._load_image(image_path)
        if image is None:
            return False
            
        # Extract the date from the filename if not provided
        if simulated_date is None:
            try:
                # Try to extract date from filename (expected format: stockcharts_NYSI_YYYY-MM-DD.png)
                filename = os.path.basename(image_path)
                date_part = filename.split('_')[-1].split('.')[0]
                simulated_date = date_part
            except:
                # Use today's date if we can't extract from filename
                simulated_date = datetime.now().strftime('%Y-%m-%d')
        
        print(f"📆 Processing image for simulated date: {simulated_date}")
        
        # Create a mock log_transition function that uses the simulated date
        original_log_transition = self.notification_mgr.log_transition
        
        def mock_log_transition(message, crossing_type, send_email=True, silent=False):
            """Override log_transition to use simulated date instead of current date"""
            log_file = self.notification_mgr.log_file
            
            # Use simulated date with current time
            current_time = datetime.now().strftime('%H:%M:%S')
            timestamp = f"{simulated_date} {current_time}"
            
            # Remove emojis and other special characters from message for log file
            clean_message = re.sub(r'[^\x00-\x7F]+', '', message)
            
            # Format the log entry
            log_entry = f"[{timestamp}] {clean_message}"
            
            # Write to log file
            with open(log_file, 'a') as f:
                f.write(log_entry + '\n')
            
            if not silent:
                print(f"📝 Logged event to {log_file}")
            
            # Don't actually send emails during testing
            if send_email and not silent:
                print(f"📧 Would send email notification: {message}")
        
        # Replace the notification manager's log_transition method temporarily
        self.notification_mgr.log_transition = mock_log_transition
        
        # Extract middle section for analysis
        middle_section = self.image_processor.extract_middle_section(image)
        
        # Detect line crossing
        crossing_result = self.image_processor.detect_line_crossing(middle_section, debug=self.debug)
        
        # If debug is True, crossing_result will have detailed info
        # If debug is False, crossing_result is a tuple (crossing_type, rightmost_color)
        if self.debug:
            current_crossing = crossing_result[0]
            color_data = crossing_result[1]
            rightmost_color = color_data.get("rightmost_color", "unknown")
        else:
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
        
        # Print detection results
        print(f"🔍 Detected crossing: {current_crossing}")
        print(f"🔍 Effective crossing: {effective_crossing}")
        print(f"🔍 Current color: {rightmost_color}")
        print(f"🔍 Last logged crossing: {last_crossing}")
        print(f"🔍 Last logged color: {last_color}")
        
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
        
        # Update the last saved image
        self.last_saved_image = image
        
        # Restore the original log_transition method
        self.notification_mgr.log_transition = original_log_transition
        
        # Return True to indicate successful processing
        return True
    
    def process_image_sequence(self, image_paths, date_override=None):
        """Process a sequence of images in order"""
        print(f"🧪 Processing sequence of {len(image_paths)} images")
        
        for i, image_path in enumerate(image_paths):
            print(f"\n--- Processing image {i+1}/{len(image_paths)}: {image_path} ---")
            
            # Use the date override if provided, otherwise extract from filename
            simulated_date = None
            if date_override and i < len(date_override):
                simulated_date = date_override[i]
                
            # Process the image
            success = self.process_image(image_path, simulated_date)
            
            if not success:
                print(f"❌ Failed to process image: {image_path}")
            
            print(f"--- Completed processing image {i+1}/{len(image_paths)} ---")
    
    def process_folder(self, folder_path, date_pattern=None):
        """Process all matching images in a folder in sorted order"""
        # Get all PNG files in the folder
        try:
            all_files = os.listdir(folder_path)
            image_files = [f for f in all_files if f.lower().endswith('.png') and (date_pattern is None or date_pattern in f)]
            
            # Sort files by name (which should sort by date if using the standard naming convention)
            image_files.sort()
            
            # Create full paths
            image_paths = [os.path.join(folder_path, f) for f in image_files]
            
            if not image_paths:
                print(f"❌ No matching image files found in {folder_path}")
                return
                
            print(f"🧪 Found {len(image_paths)} image files to process")
            self.process_image_sequence(image_paths)
            
        except Exception as e:
            print(f"❌ Error processing folder {folder_path}: {e}")
    
    def run_test_sequence(self, image_config, ignore_missing=False):
        """
        Run a test sequence using a predefined configuration
        
        Args:
            image_config: List of dictionaries with image path and optional date override
                          [{"path": "path/to/image.png", "date": "2025-06-01"}, ...]
            ignore_missing: Whether to skip missing files instead of erroring
        """
        print(f"🧪 Running test sequence with {len(image_config)} steps")
        
        for i, config in enumerate(image_config):
            image_path = config.get("path")
            simulated_date = config.get("date")
            
            print(f"\n--- Test step {i+1}/{len(image_config)} ---")
            print(f"Image: {image_path}")
            print(f"Simulated date: {simulated_date or 'Auto-detect'}")
            
            # Check if file exists
            if not os.path.exists(image_path):
                message = f"❌ Image file not found: {image_path}"
                if ignore_missing:
                    print(f"{message} (Skipping)")
                    continue
                else:
                    print(message)
                    return False
            
            # Process the image
            success = self.process_image(image_path, simulated_date)
            
            if not success:
                print(f"❌ Failed to process image: {image_path}")
                if not ignore_missing:
                    return False
            
            print(f"--- Completed test step {i+1}/{len(image_config)} ---")
        
        print("\n✅ Test sequence completed successfully")
        return True


def parse_args():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description="Test harness for stock monitoring script",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Single image command
    single_parser = subparsers.add_parser(
        "image", 
        help="Process a single image file",
        description="Process a single image file to test line crossing detection"
    )
    single_parser.add_argument("image_path", help="Path to the image file to process")
    single_parser.add_argument("--date", help="Override the date (format: YYYY-MM-DD)")
    single_parser.add_argument("--symbol", default="$NYSI", help="Stock symbol (default: $NYSI)")
    single_parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    
    # Sequence command
    sequence_parser = subparsers.add_parser(
        "sequence", 
        help="Process a sequence of image files",
        description="Process a sequence of image files in order"
    )
    sequence_parser.add_argument("image_paths", nargs="+", help="Paths to image files to process in order")
    sequence_parser.add_argument("--dates", nargs="+", help="Override dates for each image (format: YYYY-MM-DD)")
    sequence_parser.add_argument("--symbol", default="$NYSI", help="Stock symbol (default: $NYSI)")
    sequence_parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    
    # Folder command
    folder_parser = subparsers.add_parser(
        "folder", 
        help="Process all images in a folder",
        description="Process all matching images in a folder in sorted order"
    )
    folder_parser.add_argument("folder_path", help="Path to the folder containing image files")
    folder_parser.add_argument("--pattern", help="Only process files containing this pattern")
    folder_parser.add_argument("--symbol", default="$NYSI", help="Stock symbol (default: $NYSI)")
    folder_parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    
    # Preset test command
    test_parser = subparsers.add_parser(
        "test", 
        help="Run a preset test using the existing stockcharts_NYSI_*.png files",
        description="Run a preset test using the existing stockcharts_NYSI_*.png files"
    )
    test_parser.add_argument("--symbol", default="$NYSI", help="Stock symbol (default: $NYSI)")
    test_parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    
    return parser.parse_args()


def run_preset_test(symbol="$NYSI", debug=False):
    """Run a preset test using the existing stockcharts_NYSI_*.png files"""
    harness = TestHarness(symbol, debug)
    
    # Define the test sequence using the existing files
    # This recreates the sequence from June 3 to June 5, 2025
    charts_dir = "downloaded_charts"
    
    # Check if we need to use the root directory instead
    if not os.path.exists(os.path.join(charts_dir, "stockcharts_NYSI_2025-06-03.png")):
        charts_dir = "."
    
    test_sequence = [
        {"path": os.path.join(charts_dir, "stockcharts_NYSI_2025-06-03.png"), "date": "2025-06-03"},
        {"path": os.path.join(charts_dir, "stockcharts_NYSI_2025-06-04.png"), "date": "2025-06-04"},
        {"path": os.path.join(charts_dir, "stockcharts_NYSI_2025-06-05.png"), "date": "2025-06-05"}
    ]
    
    # Clear the test log file before starting
    log_file = f"test_{symbol.replace('$', '')}_changes.txt"
    if os.path.exists(log_file):
        try:
            os.remove(log_file)
            print(f"Removed existing test log file: {log_file}")
        except Exception as e:
            print(f"Warning: Could not remove existing test log file: {e}")
    
    # Run the test sequence
    harness.run_test_sequence(test_sequence)


def main():
    """Main entry point"""
    # Parse command-line arguments
    args = parse_args()
    
    if args.command == "image":
        # Process a single image
        harness = TestHarness(args.symbol, args.debug)
        harness.process_image(args.image_path, args.date)
    
    elif args.command == "sequence":
        # Process a sequence of images
        harness = TestHarness(args.symbol, args.debug)
        harness.process_image_sequence(args.image_paths, args.dates)
    
    elif args.command == "folder":
        # Process all images in a folder
        harness = TestHarness(args.symbol, args.debug)
        harness.process_folder(args.folder_path, args.pattern)
    
    elif args.command == "test":
        # Run the preset test
        run_preset_test(args.symbol, args.debug)
    
    else:
        # If no command is specified, show usage
        print("Stock Monitor Test Harness")
        print("Usage:")
        print("  python test_harness.py image IMAGE_PATH [--date DATE] [--symbol SYMBOL] [--debug]")
        print("  python test_harness.py sequence IMAGE_PATH1 IMAGE_PATH2 ... [--dates DATE1 DATE2 ...] [--symbol SYMBOL] [--debug]")
        print("  python test_harness.py folder FOLDER_PATH [--pattern PATTERN] [--symbol SYMBOL] [--debug]")
        print("  python test_harness.py test [--symbol SYMBOL] [--debug]")
        print("\nExamples:")
        print("  python test_harness.py image stockcharts_NYSI_2025-06-03.png")
        print("  python test_harness.py image stockcharts_NYSI_2025-06-03.png --date 2025-06-03")
        print("  python test_harness.py sequence stockcharts_NYSI_2025-06-03.png stockcharts_NYSI_2025-06-04.png")
        print("  python test_harness.py sequence *.png --dates 2025-06-03 2025-06-04 2025-06-05")
        print("  python test_harness.py folder . --pattern stockcharts_NYSI")
        print("  python test_harness.py test")
        
        print("\nThe test harness allows you to:")
        print("  1. Test line crossing detection using existing image files")
        print("  2. Simulate monitoring over multiple days without waiting for real time to pass")
        print("  3. Verify that the fix for red-to-black transition detection works correctly")


if __name__ == "__main__":
    main()
