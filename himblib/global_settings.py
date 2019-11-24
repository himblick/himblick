import os

# Base raspbian image
BASE_IMAGE = "images/raspbian-buster-lite.img"

# ssh public key to install in authorized_keys
SSH_ADMIN_KEYS = [os.path.expanduser("~/.ssh/id_rsa.pub")]

# List of ESSID/password pairs for configured WiFi networks
WIFI_NETWORKS = []

# Set this to a directory used to cache intermediate bits
CACHE_DIR = None
