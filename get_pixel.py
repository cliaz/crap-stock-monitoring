import cv2
import sys

# Check for correct usage
if len(sys.argv) != 2:
    print("Usage: python pixel_inspector.py <image_file>")
    sys.exit(1)

filename = sys.argv[1]

# Load image
img = cv2.imread(filename)

if img is None:
    print(f"Error: Could not open image file '{filename}'")
    sys.exit(1)

# Mouse callback function
def show_pixel_info(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        b, g, r = img[y, x]
        print(f"Clicked at ({x}, {y}) - BGR: ({b}, {g}, {r})")

# Show image and set callback
cv2.imshow("Image", img)
cv2.setMouseCallback("Image", show_pixel_info)

cv2.waitKey(0)
cv2.destroyAllWindows()
