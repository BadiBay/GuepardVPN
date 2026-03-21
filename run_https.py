import os
import ssl
import http.server
import socketserver
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import datetime

def generate_self_signed_cert():
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, u"RU"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"Moscow"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, u"Moscow"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"Local WebApp"),
        x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
    ])

    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        # Our certificate will be valid for 10 days
        datetime.datetime.utcnow() + datetime.timedelta(days=10)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName(u"localhost")]),
        critical=False,
    ).sign(key, hashes.SHA256())

    with open("key.pem", "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        
    with open("cert.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

def run_https_server():
    os.chdir(os.path.join(os.path.dirname(__file__), 'webapp'))
    
    if not os.path.exists("cert.pem") or not os.path.exists("key.pem"):
        print("Создание самоподписанного сертификата...")
        generate_self_signed_cert()
        
    port = 8443
    handler = http.server.SimpleHTTPRequestHandler
    
    with socketserver.TCPServer(("", port), handler) as httpd:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile="cert.pem", keyfile="key.pem")
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        
        print(f"Server started on HTTPS port {port}")
        print(f"Add to .env: WEBAPP_URL=https://localhost:{port}")
        print("Press Ctrl+C to stop")
        httpd.serve_forever()

if __name__ == "__main__":
    run_https_server()
