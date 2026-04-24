DNS Rebinding PoC Instructions

This PoC requires control over a DNS name (e.g., `poc.example.com`). The PoC server is `scripts/dns_rebind_server.py`.

1) Deploy `dns_rebind_server.py` on a public host (or run locally and expose via ngrok).
2) Point `poc.example.com` to the public IP of the server.
3) Access `https://poc.example.com/` from a browser — the page loads `/image.png` from the same host.
4) Change DNS for `poc.example.com` to point to a private IP (e.g., `127.0.0.1` on the victim network) or configure a hosts-file entry on the victim machine.
5) Reload the page on the victim — the browser will now request `/image.png` at the new IP, demonstrating DNS rebinding.

Notes:
- Modern browsers apply mitigations; to test effectively you may need to control TTL and response headers.
- Use this only on systems you own or explicitly are authorized to test.
