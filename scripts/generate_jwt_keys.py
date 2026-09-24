"""Generate RSA key pair for JWT RS256 signing.

Usage:
    python scripts/generate_jwt_keys.py [--output-dir ./keys]

Outputs:
    jwt_private.pem    — RSA private key (keep secret, used by auth service to sign tokens)
    jwt_public.pem     — RSA public key (distributed to gateways/services that verify tokens)

K8s Secret usage:
    kubectl create secret generic jwt-keys \
        --from-file=jwt_private.pem=./keys/jwt_private.pem \
        --from-file=jwt_public.pem=./keys/jwt_public.pem \
        -n codegenx

Environment variables (set in K8s deployment or .env):
    JWT_ALGORITHM=RS256
    JWT_PRIVATE_KEY_PATH=/etc/jwt/jwt_private.pem   # auth service only
    JWT_PUBLIC_KEY_PATH=/etc/jwt/jwt_public.pem     # gateway + verifying services
"""

import argparse
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend


def generate_key_pair(output_dir: str) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    private_key_path = out / "jwt_private.pem"
    public_key_path = out / "jwt_public.pem"

    if private_key_path.exists() or public_key_path.exists():
        print("ERROR: Key files already exist. Delete them first or use a different output directory.")
        sys.exit(1)

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    private_key_path.write_bytes(private_pem)
    print(f"Private key written to: {private_key_path}")

    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key_path.write_bytes(public_pem)
    print(f"Public key written to:  {public_key_path}")

    print("\nK8s Secret command:")
    print(f"  kubectl create secret generic jwt-keys \\")
    print(f"      --from-file=jwt_private.pem={private_key_path} \\")
    print(f"      --from-file=jwt_public.pem={public_key_path} \\")
    print(f"      -n codegenx")

    return private_key_path, public_key_path


def main():
    parser = argparse.ArgumentParser(description="Generate RSA key pair for JWT RS256")
    parser.add_argument(
        "--output-dir",
        default="./keys",
        help="Output directory for key files (default: ./keys)",
    )
    args = parser.parse_args()
    generate_key_pair(args.output_dir)


if __name__ == "__main__":
    main()
