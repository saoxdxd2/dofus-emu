import re

def check():
    path = r"desktop/client/www/build/script.js"
    with open(path, "rb") as f:
        data = f.read()

    # Find all "sendMessage("..." or 'sendMessage('...'
    pattern = re.compile(rb'sendMessage\(["\']([^"\']+)["\']')
    matches = pattern.findall(data)
    print(f"Total sendMessage calls: {len(matches)}")
    
    non_ascii = [m for m in matches if any(b > 127 for b in m)]
    print(f"Non-ASCII sendMessage calls ({len(non_ascii)}): {non_ascii}")

    # Also search for any "Message" ending with cyrillic or non-ascii
    pattern_msg = re.compile(rb'["\']([A-Za-z0-9_\x80-\xff]+Message[\x80-\xff]*)["\']')
    msg_matches = pattern_msg.findall(data)
    non_ascii_msgs = [m for m in msg_matches if any(b > 127 for b in m)]
    print(f"Non-ASCII *Message* strings ({len(non_ascii_msgs)}): {non_ascii_msgs}")

if __name__ == "__main__":
    check()
