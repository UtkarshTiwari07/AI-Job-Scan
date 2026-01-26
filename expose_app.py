import sys
import time
import ssl
# Bypass SSL verification for ngrok download
ssl._create_default_https_context = ssl._create_unverified_context
from pyngrok import ngrok

def start_tunnel():
    # Kill any existing tunnels to be safe
    ngrok.kill()
    
    # Create the tunnel
    try:
        # Try to connect to port 5000
        tunnel = ngrok.connect(5000)
        public_url = tunnel.public_url
        print("\n" + "="*50)
        print(f"🌍 Your App is Publicly Available at:")
        print(f"{public_url}")
        print("="*50 + "\n")
        
        # Keep the script running
        print("Press Ctrl+C to stop the tunnel")
        while True:
            time.sleep(1)
            
    except Exception as e:
        print(f"Error starting tunnel: {e}")
        print("\nNOTE: If you get an authentication error, you need to sign up at ngrok.com")
        print("and run: ngrok config add-authtoken <token>")

if __name__ == "__main__":
    start_tunnel()
