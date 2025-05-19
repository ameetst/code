import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import date
import yfinance as yf

etf_one = "JUNIORBEES.NS"
etf_two = "GOLDBEES.NS"

# Get data for the ticker
data = yf.download(etf_one, start="2023-01-01")

# Display the first few rows
print(data.head())