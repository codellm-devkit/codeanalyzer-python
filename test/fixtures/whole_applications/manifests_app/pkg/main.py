import requests
import colorama  # deliberately undeclared

def fetch(url):
    return requests.get(url)
