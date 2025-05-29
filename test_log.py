#!/usr/bin/env python3
from stock_monitor import StockChartMonitor

# Create monitor instance
monitor = StockChartMonitor()

# Test log_transition with emojis
message_with_emoji = "⚠️ Line crossing detected: black_to_red"
monitor.log_transition(message_with_emoji, "black_to_red")

# Print the log file content
print("\nLog file content:")
with open("nysi_changes.txt", "r") as f:
    print(f.read())
