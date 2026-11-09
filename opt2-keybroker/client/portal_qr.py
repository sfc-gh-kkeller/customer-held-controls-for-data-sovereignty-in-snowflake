"""Print the step-up portal URL as text + terminal QR, and write a local QR page (.secrets/portal_qr.html)."""
import base64
import io
import json
import pathlib
import sys
import webbrowser

import qrcode
import qrcode.image.svg

url = json.load(open("infra/outputs.json"))["SovereigntyKeyBroker"]["StepUpPortalUrl"]
print(url)

qr = qrcode.QRCode(border=2)
qr.add_data(url)
qr.make(fit=True)
qr.print_ascii(invert=True)

buf = io.BytesIO()
qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage).save(buf)
svg = base64.b64encode(buf.getvalue()).decode()
page = pathlib.Path(".secrets/portal_qr.html")
page.parent.mkdir(exist_ok=True)
page.write_text(
    "<!doctype html><meta charset=utf-8><title>Step-up portal</title>"
    "<body style='font-family:sans-serif;text-align:center;margin-top:5vh'>"
    "<h2>Option 2 step-up portal</h2>"
    f"<img src='data:image/svg+xml;base64,{svg}' style='width:60vmin'>"
    f"<p><a href='{url}'>{url}</a></p></body>"
)
print(f"QR page: {page.resolve()}")
if "--open" in sys.argv:
    webbrowser.open(page.resolve().as_uri())
