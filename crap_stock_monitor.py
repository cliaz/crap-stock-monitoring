#!/usr/bin/env python3
"""
Crap Stock Monitor
Queries Stockcharts API directly to determine Red/Black status and sends email on color changes.
"""

import requests
import smtplib
import json
import math
from datetime import datetime, timedelta, time as dt_time, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import argparse
import os


class CrapStockMonitor:
    def __init__(self, symbol="$NYSI"):
        self.symbol = symbol
        self.state_file = f"{symbol.replace('$', '')}_state.json"
        self.user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'

    # Fetch recent NYSI data from StockCharts API
    def get_nysi_data(self, days=5):
        
        url = f'https://stockcharts.com/c-sc/sc?s={self.symbol}&p=D&yr=0&mn=0&dy={days}&i=t3757734781c&img=text&inspector=yes'
        
        try:
            response = requests.get(url, headers={'user-agent': self.user_agent}, timeout=15)
            response.raise_for_status()
            
            # Extract data between <pricedata> tags
            text = response.text
            if '<pricedata>' not in text:
                print("ERROR: No price data found in response")
                return None
                
            # Find the start and end of pricedata section
            start = text.find('<pricedata>') + len('<pricedata>')
            end = text.find('</pricedata>', start)
            if end == -1:
                # If there's no closing tag, take everything to the end
                data_section = text[start:]
            else:
                data_section = text[start:end]
            
            # Parse the data, which is pipe-separated lines
            # eg <pricedata>83 202509180930 202509181600 631.29 0|218 202509190930 202509191600 620.74 0</pricedata>
            lines = data_section.split('|')
            nysi_values = []
            
            for line in lines:
                cols = line.strip().split(' ')
                if len(cols) < 4:
                    continue
                    
                try:
                    date_str = cols[1]
                    date = datetime(int(date_str[0:4]), int(date_str[4:6]), int(date_str[6:8]))
                    value = float(cols[3])
                    
                    if not math.isnan(value):
                        nysi_values.append({
                            'date': date.strftime('%Y-%m-%d'),
                            'value': value
                        })
                except (ValueError, IndexError):
                    continue
            
            # Sort by date to ensure chronological order
            nysi_values.sort(key=lambda x: x['date'])
            return nysi_values
            
        except requests.exceptions.RequestException as e:
            print(f"ERROR: Error fetching NYSI data: {e}")
            return None

    # Determine if NYSI is Red or Black based on latest trend
    def determine_color(self, nysi_values):
        
        if len(nysi_values) < 2:
            return None
            
        # Get the two most recent values
        latest = nysi_values[-1]['value']
        previous = nysi_values[-2]['value']
        
        if latest > previous:
            return "Black"  # Going up = Black
        elif latest < previous:
            return "Red"    # Going down = Red
        else:
            # If same value (rare if not impossible), look further back to determine trend
            for i in range(len(nysi_values) - 2, 0, -1):
                if nysi_values[i]['value'] != nysi_values[i-1]['value']:
                    if nysi_values[i]['value'] > nysi_values[i-1]['value']:
                        return "Black"
                    else:
                        return "Red"
            return None  # All values are the same

    # Load the last known state from the state file
    def load_last_state(self):
        
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    return state.get('last_color'), state.get('last_date'), state.get('last_value')
            except (json.JSONDecodeError, IOError):
                pass
        return None, None, None

    # Save the current state to the state file
    def save_state(self, color, date, value):

        state = {
            'last_color': color,
            'last_date': date,
            'last_value': value,
            'updated': datetime.now().isoformat()
        }
        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except IOError as e:
            print(f"WARNING: Could not save state: {e}")

    # Validate email credentials without sending an email
    def validate_email_credentials(self):
        """
        Validates email credentials by attempting to authenticate with the SMTP server.
        Returns True if credentials are valid, False otherwise.
        """
        if not os.path.exists("email_details.py"):
            print("WARNING: Email validation failed: email_details.py not found")
            return False

        try:
            from email_details import sender_email, sender_password_password, recipients
            
            if not sender_email or not sender_password_password:
                print("WARNING: Email validation failed: Missing email credentials")
                return False
                
            print("Validating email credentials...")
            
            # Test connection and authentication
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(sender_email, sender_password_password)
            server.quit()
            
            print("Email credentials validated successfully")
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            print(f"ERROR: Email authentication failed - Invalid credentials: {e}")
            return False
        except smtplib.SMTPException as e:
            print(f"ERROR: Email validation failed - SMTP error: {e}")
            return False
        except Exception as e:
            print(f"ERROR: Email validation failed: {e}")
            return False

    # Send email notification about color change. see email_details.py for config
    def send_email(self, old_color, new_color, nysi_value, date):
      
        if not os.path.exists("email_details.py"):
            print("WARNING: Email notifications disabled: email_details.py not found")
            return False

        try:
            from email_details import sender_email, sender_password_password, recipients
            
            if not sender_email or not sender_password_password:
                print("WARNING: Email notification skipped: Missing email credentials")
                return False
                
            if not recipients:
                recipients = [sender_email]
            
            # Create email
            subject = f"NYSI Alert: Color Changed from {old_color} to {new_color}"
            
            msg = MIMEMultipart()
            msg['From'] = sender_email
            msg['To'] = ", ".join(recipients)
            msg['Subject'] = subject
            
            # Create email body
            html = f"""
            <html>
            <body>
                <h2>NYSI Color Change Alert</h2>
                <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p><strong>Symbol:</strong> {self.symbol}</p>
                <p><strong>Change:</strong> {old_color} → {new_color}</p>
                <p><strong>Current NYSI Value:</strong> {nysi_value}</p>
                <p><strong>Data Date:</strong> {date}</p>
                <br>
                <p>This is an automated alert from your Crap Stock Monitor.</p>
            </body>
            </html>
            """
            
            msg.attach(MIMEText(html, 'html'))
            
            # Send email
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(sender_email, sender_password_password)
            server.send_message(msg)
            server.quit()
            
            print(f"Email notification sent to {', '.join(recipients)}")
            return True
            
        except Exception as e:
            print(f"ERROR: Failed to send email: {e}")
            return False

    # Check NYSI status once and send email if changed
    def check_once(self):

        print(f"Checking NYSI status at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Get recent NYSI data
        nysi_data = self.get_nysi_data()
        if not nysi_data:
            print("ERROR: Could not fetch NYSI data")
            return False
            
        # Determine current color
        current_color = self.determine_color(nysi_data)
        if not current_color:
            print("ERROR: Could not determine NYSI color")
            return False
            
        latest_data = nysi_data[-1]
        current_date = latest_data['date']
        current_value = latest_data['value']
        
        print(f"Current NYSI: {current_value} ({current_date}) = {current_color}")
        
        # Load last known state
        last_color, last_date, last_value = self.load_last_state()
        
        if last_color:
            print(f"Previous state: {last_value} ({last_date}) = {last_color}")
            
            if current_color != last_color:
                print(f"Color change detected: {last_color} -> {current_color}")
                
                # Send email notification
                email_sent = self.send_email(last_color, current_color, current_value, current_date)
                
                # Save new state
                self.save_state(current_color, current_date, current_value)
                
                if email_sent:
                    print("Notification sent successfully")
                else:
                    print("WARNING: Notification failed to send")
                    
                return True
            else:
                print("No color change detected")
                # Update state with latest data even if color hasn't changed
                self.save_state(current_color, current_date, current_value)
                return True
        else:
            print("No previous state found - initializing")
            self.save_state(current_color, current_date, current_value)
            print("Initial state saved")
            return True

    # Check if current time is within the monitoring window
    def is_within_monitoring_window(self, start_time, end_time):

        if start_time is None or end_time is None:
            return True  # No window specified, always monitor
            
        now = datetime.now().time()
        
        # Handle cases where window crosses midnight
        if start_time <= end_time:
            # Normal case: 09:00 - 17:00
            return start_time <= now <= end_time
        else:
            # Crosses midnight: 23:00 - 06:00
            return now >= start_time or now <= end_time

    # Calculate seconds until the next monitoring window starts
    def calculate_time_until_next_window(self, start_time, end_time):
        
        if start_time is None or end_time is None:
            return 0  # No window specified
            
        now = datetime.now()
        current_time = now.time()
        today = now.date()
        
        # Create datetime objects for window start today and tomorrow
        window_start_today = datetime.combine(today, start_time)
        window_start_tomorrow = datetime.combine(today + timedelta(days=1), start_time)
        
        if start_time <= end_time:
            # Normal window (doesn't cross midnight)
            if current_time < start_time:
                # Window hasn't started today
                return int((window_start_today - now).total_seconds())
            else:
                # Window already passed today, wait for tomorrow
                return int((window_start_tomorrow - now).total_seconds())
        else:
            # Window crosses midnight
            if current_time >= start_time:
                # We're in the late part of today's window, next window starts tomorrow
                return int((window_start_tomorrow - now).total_seconds())
            elif current_time <= end_time:
                # We're in the early part of today's window (after midnight), next window starts today
                return int((window_start_today - now).total_seconds())
            else:
                # We're between windows, next window starts today
                return int((window_start_today - now).total_seconds())

    # Check if we have data for today's date
    def has_received_data_today(self, nysi_data):
        
        if not nysi_data:
            return False
            
        today = date.today().strftime('%Y-%m-%d')
        latest_data_date = nysi_data[-1]['date']
        
        return latest_data_date == today

    # Main monitoring loop
    def monitor(self, check_interval=300, monitoring_window=None):  # 5 minutes default
        
        print(f"Starting Crap Stock Monitor for {self.symbol}")
        print(f"Check interval: {check_interval} seconds")
        
        if monitoring_window:
            start_time, end_time = monitoring_window
            print(f"Monitoring window: {start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}")
        else:
            print("Monitoring continuously (no time window)")
            
        print("Press Ctrl+C to stop monitoring")
        
        # Track if we were in the monitoring window in the previous iteration
        was_in_window = False
        data_received_today = False
        
        try:
            while True:
                # Check if we're within the monitoring window
                start_time, end_time = monitoring_window if monitoring_window else (None, None)
                in_window = self.is_within_monitoring_window(start_time, end_time)
                
                # Reset data_received_today flag at the start of each new day
                current_date = date.today()
                if hasattr(self, '_last_check_date') and self._last_check_date != current_date:
                    data_received_today = False
                self._last_check_date = current_date
                
                # Log entering the monitoring window
                if in_window and not was_in_window and monitoring_window:
                    current_time = datetime.now().strftime('%H:%M:%S')
                    print(f"[{current_time}] Entering monitoring window")
                
                # Log exiting the monitoring window
                if not in_window and was_in_window and monitoring_window:
                    current_time = datetime.now().strftime('%H:%M:%S')
                    print(f"[{current_time}] Exiting monitoring window")
                
                # Update window state for next iteration
                was_in_window = in_window
                
                if in_window and not data_received_today:
                    # We're in the monitoring window and haven't received data today
                    nysi_data = self.get_nysi_data()
                    if nysi_data and self.has_received_data_today(nysi_data):
                        print("New data received for today - processing...")
                        self.check_once()
                        data_received_today = True
                        print("Data processed. No more checks needed today.")
                    else:
                        print("No new data available yet. Will check again in next interval.")
                        print(f"Waiting {check_interval} seconds until next check...\n")
                        time.sleep(check_interval)
                        continue
                elif in_window and data_received_today:
                    # We're in window but already processed today's data
                    print("Today's data already processed. Waiting until tomorrow...")
                    # Calculate wait time until next day's window starts
                    wait_seconds = self.calculate_time_until_next_window(start_time, end_time)
                    if wait_seconds > 0:
                        hours = wait_seconds // 3600
                        minutes = (wait_seconds % 3600) // 60
                        print(f"Sleeping for {hours}h {minutes}m until next monitoring window")
                        time.sleep(wait_seconds)
                        continue
                elif not in_window and monitoring_window:
                    # Outside monitoring window - calculate wait time until next window
                    current_time = datetime.now().strftime('%H:%M:%S')
                    window_str = f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}"
                    print(f"[{current_time}] Outside monitoring window ({window_str})")
                    
                    wait_seconds = self.calculate_time_until_next_window(start_time, end_time)
                    if wait_seconds > 0:
                        hours = wait_seconds // 3600
                        minutes = (wait_seconds % 3600) // 60
                        print(f"Sleeping for {hours}h {minutes}m until next monitoring window")
                        time.sleep(wait_seconds)
                        continue
                else:
                    # No monitoring window specified, check normally
                    self.check_once()
                    print(f"Waiting {check_interval} seconds until next check...\n")
                
                time.sleep(check_interval)
                
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user")
            print("Crap Stock Monitor stopped")

def parse_time(time_str):
    """Parse time string in HH:MM format"""
    try:
        hour, minute = map(int, time_str.split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Invalid time range")
        return dt_time(hour, minute)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid time format: {time_str}. Use HH:MM format (e.g., 09:30)")

def main():
    parser = argparse.ArgumentParser(description="Crap Stock Monitor")
    parser.add_argument("--symbol", default="$NYSI", help="Stock symbol to monitor (default: $NYSI)")
    parser.add_argument("--check", action="store_true", help="Check once and exit")
    parser.add_argument("--validate-email", action="store_true", help="Validate email credentials and exit")
    parser.add_argument("--interval", type=int, default=300, help="Check interval in seconds (default: 300)")
    parser.add_argument("--window", type=str, metavar="START-END", 
                        help="Monitoring window (format: HH:MM-HH:MM, e.g., 09:30-16:00)")
    
    args = parser.parse_args()
    
    # Parse monitoring window if provided
    monitoring_window = None
    if args.window:
        try:
            start_str, end_str = args.window.split('-')
            start_time = parse_time(start_str.strip())
            end_time = parse_time(end_str.strip())
            monitoring_window = (start_time, end_time)
        except ValueError:
            print("ERROR: Invalid window format. Use HH:MM-HH:MM (e.g., 09:30-16:00)")
            return
    
    monitor = CrapStockMonitor(args.symbol)
    
    print("Starting Crap Stock Monitor")
    
    # Handle email validation only mode
    if args.validate_email:
        if os.path.exists("email_details.py"):
            success = monitor.validate_email_credentials()
            print("Crap Stock Monitor stopped")
            exit(0 if success else 1)
        else:
            print("ERROR: email_details.py not found")
            print("Crap Stock Monitor stopped")
            exit(1)
    
    # Validate email credentials if email_details.py exists (for normal operation)
    if os.path.exists("email_details.py"):
        if not monitor.validate_email_credentials():
            print("WARNING: Continuing with invalid email credentials. Notifications may fail.")
    else:
        print("INFO: No email_details.py found. Email notifications will be disabled.")
    
    if args.check:
        monitor.check_once()
        print("Crap Stock Monitor stopped")
    else:
        monitor.monitor(args.interval, monitoring_window)


if __name__ == "__main__":
    main()