# Stock Chart Monitor

This application monitors StockCharts.com for color changes in the $NYSI chart line (red/black transitions). It analyzes a specific region of the chart to detect when the line changes from red to black or black to red, logs these transitions, and sends email notifications.

## Non-Docker Usage

### Requirements

- Python 3.6+
- Required packages: see requirements.txt

### Installation

```bash
pip install -r requirements.txt
```

### Running

Same commands as above, but without docker-compose:

```bash
python stock_monitor.py test
python stock_monitor.py monitor 60
python stock_monitor.py monitor 60 continuous
```
