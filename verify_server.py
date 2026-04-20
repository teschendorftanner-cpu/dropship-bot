"""
Handles eBay's endpoint verification challenge.
"""
import hashlib
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
load_dotenv()

VERIFICATION_TOKEN = os.getenv("EBAY_VERIFICATION_TOKEN", "")
ENDPOINT_URL = os.getenv("EBAY_ENDPOINT_URL", "")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        challenge_code = params.get("challenge_code", [None])[0]

        print(f"\nIncoming request: {self.path}")
        print(f"Challenge code: {challenge_code}")
        print(f"Using endpoint URL: {ENDPOINT_URL}")
        print(f"Using token: {VERIFICATION_TOKEN}")

        if challenge_code:
            combined = challenge_code + VERIFICATION_TOKEN + ENDPOINT_URL
            hash_val = hashlib.sha256(combined.encode()).hexdigest()
            print(f"Hash: {hash_val}")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(f'{{"challengeResponse":"{hash_val}"}}'.encode())
            print("✅ Responded to eBay challenge!")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

    def do_POST(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if not VERIFICATION_TOKEN:
        print("❌ EBAY_VERIFICATION_TOKEN not set in .env")
        exit(1)
    if not ENDPOINT_URL:
        print("❌ EBAY_ENDPOINT_URL not set in .env")
        exit(1)

    print(f"Verification token : {VERIFICATION_TOKEN}")
    print(f"Endpoint URL       : {ENDPOINT_URL}")
    print("\nServer running on port 8080...\n")
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
