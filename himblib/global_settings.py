import os

# Base raspbian image
BASE_IMAGE = "images/raspbian-buster-lite.img"

# ssh public key to install in authorized_keys
SSH_ADMIN_KEYS = [os.path.expanduser("~/.ssh/id_rsa.pub")]

# Himblick /boot/wifi.ini configuration
WIFI_CONFIG = None

# Set this to a directory used to cache intermediate bits
CACHE_DIR = None

# Tarball with ssh host keys to reuse
# If None, generate random ones
SSH_HOST_KEYS = None

# Himblick Debian package to install in the raspbian system
HIMBLICK_PACKAGE = "../himblick_1.0-1_all.deb"

# Keyboard layout to configure
KEYBOARD_LAYOUT = "us"

# Timezone to configure
TIMEZONE = "Europe/Berlin"

# Host name
HOSTNAME = "himblick"
