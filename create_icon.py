#!/usr/bin/env python3
"""Generate 96x96 PNG icon from scratch using PIL"""

from PIL import Image, ImageDraw, ImageFont
import sys

# Create 96x96 image with gradient background
img = Image.new('RGB', (96, 96), color='white')
draw = ImageDraw.Draw(img)

# Create gradient blue background
for y in range(96):
    # Gradient from #00d4ff to #0066ff
    r = int(0 + (0 - 0) * y / 96)
    g = int(212 - (212 - 102) * y / 96)
    b = int(255 - (255 - 255) * y / 96)
    draw.rectangle([(0, y), (96, y+1)], fill=(r, g, b))

# Draw rounded corners by filling corners with transparency
# Create mask for rounded corners
mask = Image.new('L', (96, 96), 0)
mask_draw = ImageDraw.Draw(mask)
mask_draw.rounded_rectangle([(0, 0), (96, 96)], radius=20, fill=255)

# Apply mask
output = Image.new('RGBA', (96, 96), (0, 0, 0, 0))
output.paste(img, (0, 0))
output.putalpha(mask)

draw = ImageDraw.Draw(output)

# Draw large "S" letter
try:
    # Try to use a bold font
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 58)
except:
    try:
        font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 58)
    except:
        # Fallback to default
        font = ImageFont.load_default()

# Draw "S" centered
text = "S"
bbox = draw.textbbox((0, 0), text, font=font)
text_width = bbox[2] - bbox[0]
text_height = bbox[3] - bbox[1]
x = (96 - text_width) // 2
y = (70 - text_height) // 2 + 5
draw.text((x, y), text, fill='white', font=font)

# Draw subtitle lines (representing SRT format)
# Long line
draw.rounded_rectangle([(19, 76), (77, 82)], radius=3, fill=(255, 255, 255, 200))
# Short line
draw.rounded_rectangle([(24, 84), (72, 88)], radius=2, fill=(255, 255, 255, 150))

# Save as PNG
output.save('/mnt/user/appdata/SRTGEN/icon-96.png', 'PNG')
print("✓ Created icon-96.png (96x96)")
