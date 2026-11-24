"""One-time safe integration changes for the original standalone HTML."""
from pathlib import Path

path = Path(__file__).resolve().parent.parent / "frontend" / "digital_inventory_and_sales_management_system.html"
text = path.read_text(encoding="utf-8")

if '<script src="/api.js"></script>' not in text:
    text = text.replace("</body>", '    <script src="/api.js"></script>\n</body>', 1)

# Existing HTML uses innerHTML for customer text and product attributes.
d = "$" + "{"
for item in ("item.name", "item.category", "i.name", "b.customer", "b.payment", "cat"):
    text = text.replace(d + item + "}", d + "escapeHtml(" + item + ")}")

original = 'onclick="selectPosCategory(\'' + d + 'escapeHtml(cat)}\')"'
encoded = 'onclick="selectPosCategory(decodeURIComponent(\'' + d + "encodeURIComponent(cat).replace(/'/g, '%27')}" + '\'))"'
text = text.replace(original, encoded)

text = text.replace("Est. Net Profit (24%)", "Profit (requires costs)")
text = text.replace("Healthy retail margin", "Add cost prices to calculate profit")
text = text.replace("+14.2% vs yesterday", "Based on saved invoices")

path.write_text(text, encoding="utf-8")
print("Integrated /api.js and escaped dynamic HTML text")
