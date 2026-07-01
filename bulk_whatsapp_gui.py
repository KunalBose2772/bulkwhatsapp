import multiprocessing
import sys
# Enable freeze support immediately for PyInstaller compatibility with multiprocessing in undetected-chromedriver
if __name__ == '__main__':
    multiprocessing.freeze_support()

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
import time
import csv
import threading
import queue
import os
import random
import pyperclip
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException, TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc

# --- Thread-Safe GUI Queue ---
gui_queue = queue.Queue()

# --- Global Variables ---
numbers = []
message_template = ""
driver = None
stop_event = threading.Event()
image_path = None

total_numbers_loaded = 0
messages_sent = 0
messages_skipped = 0

# --- Resilient XPaths/Selectors ---
XPATHS_NEW_CHAT = [
    "//span[@data-icon='new-chat-outline']",
    "//div[@title='New chat']",
    "//button[@aria-label='New chat']",
    "//span[@data-icon='chat']"
]

XPATHS_SEARCH_BOX = [
    "//div[@contenteditable='true' and @aria-label='Search name or number']",
    "//div[contains(@aria-label, 'Search name or number')]",
    "//*[contains(@aria-label, 'Search name or number')]",
    "//*[contains(@placeholder, 'Search name or number')]"
]

XPATHS_CHAT_HEADER = [
    "//header//span[@dir='auto' and @data-testid='conversation-info-header-chat-title']",
    "//header//span[@dir='auto']"
]

XPATHS_ATTACH_BTN = [
    "//span[@data-icon='plus']",
    "//span[@data-icon='attach-menu-plus']",
    "//button[@title='Attach']",
    "//div[@title='Attach']",
    "//div[@aria-label='Attach']",
    "//button[@aria-label='Attach']",
    "//span[@data-icon='attach']",
    "//*[@data-icon='plus']",
    "//*[@data-icon='attach']"
]

XPATHS_FILE_INPUT = [
    "//input[@accept='image/*,video/mp4,video/3gpp,video/quicktime']",
    "//input[@type='file']"
]

XPATHS_CAPTION_BOX = [
    "//*[@data-testid='media-caption-input']",
    "//div[@data-testid='media-caption-input']",
    "//div[@aria-label='Add a caption' and @contenteditable='true']",
    "//div[contains(@aria-label, 'caption') and @contenteditable='true']",
    "//div[@contenteditable='true' and @role='textbox']",
    "//div[@contenteditable='true']"
]

XPATHS_TEXT_BOX = [
    "//div[@aria-label='Type a message' and @contenteditable='true']",
    "//div[@contenteditable='true' and @data-tab='10']",
    "//footer//div[@contenteditable='true']"
]

XPATHS_SEND_BTN = [
    "//*[@data-testid='send']",
    "//span[@data-testid='send']",
    "//div[@data-testid='send']",
    "//span[@data-icon='send']",
    "//span[@data-icon='send-light']",
    "//*[@data-icon='send-light']",
    "//div[@role='button' and .//span[@data-icon='send']]",
    "//div[@role='button' and .//*[@data-icon='send']]",
    "//button[.//span[@data-icon='send']]",
    "//button[@aria-label='Send']",
    "//div[@aria-label='Send']",
    "//*[@data-icon='send']",
    "//*[@role='button' and @aria-label='Send']"
]

# --- Dynamic Path Resolution for Portability ---
def get_profile_path():
    local_appdata = os.environ.get('LOCALAPPDATA')
    if not local_appdata:
        local_appdata = os.path.expanduser('~')
    profile_dir = os.path.join(local_appdata, "BulkWhatsApp", "chrome_profile")
    try:
        os.makedirs(profile_dir, exist_ok=True)
    except Exception:
        # Fallback to local execution directory if AppData is read-only
        profile_dir = os.path.join(os.getcwd(), "whatsapp_profile")
        os.makedirs(profile_dir, exist_ok=True)
    return os.path.abspath(profile_dir)

# --- Thread-Safe Logging & Stats ---
def log(msg):
    gui_queue.put(('log', msg))

def update_stats_bar(total, sent, skipped, progress):
    gui_queue.put(('stats', (total, sent, skipped, progress)))

# --- Element Helper Functions ---
def find_element_with_fallbacks(driver, xpath_list, timeout=10):
    wait = WebDriverWait(driver, timeout)
    for xpath in xpath_list:
        try:
            return wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
        except Exception:
            continue
    raise TimeoutException(f"Could not locate element using selectors: {xpath_list}")

def click_element_with_fallbacks(driver, xpath_list, timeout=10):
    wait = WebDriverWait(driver, timeout)
    for xpath in xpath_list:
        try:
            element = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            try:
                element.click()
                return element
            except Exception:
                driver.execute_script("arguments[0].click();", element)
                return element
        except Exception:
            continue
    raise TimeoutException(f"Could not click element using selectors: {xpath_list}")

def clear_search_bar(driver):
    try:
        search_box = find_element_with_fallbacks(driver, XPATHS_SEARCH_BOX, timeout=2)
        search_box.clear()
        search_box.send_keys(Keys.CONTROL, 'a')
        search_box.send_keys(Keys.DELETE)
    except Exception:
        pass

# --- Core Business Logic Functions ---
def load_csv():
    global numbers, total_numbers_loaded
    file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
    if file_path:
        try:
            # Use utf-8-sig to automatically strip potential BOM marks from Excel CSVs
            with open(file_path, newline='', encoding='utf-8-sig') as csvfile:
                reader = csv.reader(csvfile)
                numbers = [row[0].strip() for row in reader if row and row[0].strip()]
            total_numbers_loaded = len(numbers)
            gui_queue.put(('stats', (total_numbers_loaded, messages_sent, messages_skipped, 0)))
            log(f"✅ Loaded {len(numbers)} numbers from CSV")
        except Exception as e:
            log(f"❌ Error reading CSV file: {e}")

def open_whatsapp_thread():
    global driver
    try:
        chrome_options = uc.ChromeOptions()
        profile_path = get_profile_path()
        chrome_options.add_argument(f"--user-data-dir={profile_path}")
        chrome_options.add_argument("--profile-directory=Default")
        chrome_options.add_argument("--disable-profile-picker")
        chrome_options.add_argument("--no-first-run")
        chrome_options.add_argument("--skip-first-run-ui")
        chrome_options.add_argument("--no-default-browser-check")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--start-maximized")

        log("🚀 Launching Chrome with WhatsApp Web session in background...")
        
        # Determine the installed Chrome major version to avoid ChromeDriver mismatch errors
        chrome_version = None
        
        # 1. Try reading the registry (fastest and most standard)
        try:
            import winreg
            reg_keys = [
                (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon")
            ]
            for hkey, subkey in reg_keys:
                try:
                    with winreg.OpenKey(hkey, subkey) as key:
                        version, _ = winreg.QueryValueEx(key, "version")
                        if version:
                            chrome_version = int(version.split('.')[0])
                            break
                except Exception:
                    continue
        except Exception:
            pass

        # 2. Fallback to querying file properties via PowerShell if registry was unsuccessful
        if not chrome_version:
            import subprocess
            paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe")
            ]
            for p in paths:
                if os.path.exists(p):
                    try:
                        cmd = f'(Get-Item "{p}").VersionInfo.ProductVersion'
                        # Use CREATE_NO_WINDOW (0x08000000) to prevent a black cmd window from flashing
                        output = subprocess.check_output(["powershell", "-Command", cmd], creationflags=0x08000000).decode("utf-8").strip()
                        if output:
                            chrome_version = int(output.split('.')[0])
                            break
                    except Exception:
                        continue

        if chrome_version:
            log(f"Detected Chrome major version: {chrome_version}")
            driver = uc.Chrome(options=chrome_options, version_main=chrome_version)
        else:
            log("⚠️ Could not detect Chrome version. Attempting default launch...")
            driver = uc.Chrome(options=chrome_options)
            
        driver.get("https://web.whatsapp.com")
        log("🌐 WhatsApp Web page loaded. Monitoring login status...")

        # Monitor login state in a loop
        logged_in = False
        qr_logged = False
        
        for _ in range(120): # Monitor for up to 3 minutes (120 * 1.5s = 180s)
            if not driver:
                break
            try:
                # Check if logged in (new chat button or search bar or side pane exists)
                is_logged_in = False
                for xpath in XPATHS_NEW_CHAT + XPATHS_SEARCH_BOX:
                    try:
                        driver.find_element(By.XPATH, xpath)
                        is_logged_in = True
                        break
                    except Exception:
                        continue
                
                if is_logged_in:
                    if not logged_in:
                        log("✅ WhatsApp logged in successfully!")
                        logged_in = True
                    # Check if chats are loaded (pane-side or contact cards)
                    try:
                        driver.find_element(By.XPATH, "//div[@id='pane-side']")
                        log("💬 Chats loaded and ready!")
                        break
                    except Exception:
                        pass
                else:
                    # Check if QR code is visible
                    try:
                        driver.find_element(By.CSS_SELECTOR, "canvas[aria-label='Scan me!']")
                        if not qr_logged:
                            log("📷 QR Code detected. Please scan using your WhatsApp app.")
                            qr_logged = True
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(1.5)
            
    except Exception as e:
        log(f"❌ Error loading Chrome/WhatsApp: {e}")
        log("💡 Note: If Chrome is already running under this profile, close it and retry.")
    finally:
        gui_queue.put(('enable_open_whatsapp', None))

def open_whatsapp():
    # Start launching Chrome in a background thread to prevent Tkinter window from freezing
    btn_open_whatsapp.config(state=tk.DISABLED, bg="#9ca3af")
    thread = threading.Thread(target=open_whatsapp_thread)
    thread.daemon = True
    thread.start()

def remove_image():
    global image_path
    image_path = None
    gui_queue.put(('disable_remove_image', None))
    log("🖼️ Image attachment removed.")

def select_image():
    global image_path
    file_path = filedialog.askopenfilename(filetypes=[("Image files", "*.png;*.jpg;*.jpeg;*.gif;*.bmp")])
    if file_path:
        image_path = file_path
        log(f"🖼️ Selected image: {image_path}")
        gui_queue.put(('enable_remove_image', None))
    else:
        image_path = None
        log("🖼️ No image selected.")
        gui_queue.put(('disable_remove_image', None))

def stop_sending():
    stop_event.set()
    log("⏹️ Stop requested. Waiting for the current dispatch to safely terminate...")

def send_messages_worker():
    global driver, numbers, message_template, messages_sent, messages_skipped, total_numbers_loaded
    try:
        stop_event.clear()
        messages_sent = 0
        messages_skipped = 0
        
        if not driver:
            log("❌ Error: WhatsApp is not opened. Click 'Open WhatsApp Web' first.")
            gui_queue.put(('reset_buttons', None))
            return

        # Fetch values safely from the UI variables populated right before thread launch
        if not message_template and not image_path:
            log("⚠️ Warning: Both message template and image are empty. Please enter content to send.")
            gui_queue.put(('reset_buttons', None))
            return

        total_numbers_loaded = len(numbers)
        update_stats_bar(total_numbers_loaded, messages_sent, messages_skipped, 0)
        log(f"📤 Starting dispatch to {total_numbers_loaded} recipients...")

        for idx, number in enumerate(numbers):
            if stop_event.is_set():
                log("⏹️ Sending execution stopped by user.")
                break

            log(f"➡️ Processing contact {idx+1}/{total_numbers_loaded}: {number}")
            try:
                # 1. Clear search bar in preparation
                clear_search_bar(driver)
                time.sleep(0.5)

                # 2. Open new chat dialog
                try:
                    # Attempt to close overlay notifications if any are blocking the UI
                    try:
                        notif_close = driver.find_element(By.XPATH, "//div[@role='button' and @aria-label='Close']")
                        driver.execute_script("arguments[0].click();", notif_close)
                        time.sleep(0.3)
                    except Exception:
                        pass
                    
                    click_element_with_fallbacks(driver, XPATHS_NEW_CHAT, timeout=8)
                except Exception as e:
                    log(f"⚠️ Failed to open new chat panel. Skipping contact {number}.")
                    messages_skipped += 1
                    update_stats_bar(total_numbers_loaded, messages_sent, messages_skipped, idx + 1)
                    continue

                # 3. Enter contact number in search box
                try:
                    search_box = find_element_with_fallbacks(driver, XPATHS_SEARCH_BOX, timeout=5)
                    try:
                        search_box.click()
                    except Exception:
                        pass
                    
                    # Safely clear standard inputs
                    try:
                        search_box.clear()
                    except Exception:
                        pass
                    
                    # Safely clear rich text contenteditable divs
                    try:
                        search_box.send_keys(Keys.CONTROL, 'a')
                        search_box.send_keys(Keys.DELETE)
                    except Exception:
                        pass
                    
                    search_box.send_keys(number)
                    time.sleep(1.5)
                    search_box.send_keys(Keys.ENTER)
                    time.sleep(1.5)
                except Exception as e:
                    log(f"⚠️ Search failed or timed out. Skipping contact {number}. Error: {e}")
                    messages_skipped += 1
                    update_stats_bar(total_numbers_loaded, messages_sent, messages_skipped, idx + 1)
                    continue

                # 4. Verify if chat opened successfully (New Chat panel's Back button should disappear if successful)
                panel_still_open = False
                for _ in range(6):  # Poll 6 times (6 * 0.5s = 3.0s maximum wait)
                    back_btn_found = False
                    for xpath in ["//button[@aria-label='Back']", "//span[@data-icon='back']", "//span[@data-icon='back-light']"]:
                        try:
                            back_el = driver.find_element(By.XPATH, xpath)
                            if back_el.is_displayed():
                                back_btn_found = True
                                break
                        except Exception:
                            continue
                    if not back_btn_found:
                        panel_still_open = False
                        break
                    else:
                        panel_still_open = True
                        time.sleep(0.5)
                
                if panel_still_open:
                    log(f"⏭️ {number} is not registered on WhatsApp or search failed. Skipped.")
                    messages_skipped += 1
                    update_stats_bar(total_numbers_loaded, messages_sent, messages_skipped, idx + 1)
                    # Close the side panel to reset UI state for next search
                    try:
                        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                    except Exception:
                        pass
                    try:
                        back_btn = driver.find_element(By.XPATH, "//button[@aria-label='Back']|//span[@data-icon='back']")
                        back_btn.click()
                    except Exception:
                        pass
                    time.sleep(1.0)
                    continue

                # 5. Dispatch message
                if image_path:
                    try:
                        # Open attachment menu
                        click_element_with_fallbacks(driver, XPATHS_ATTACH_BTN, timeout=5)
                        time.sleep(random.uniform(0.8, 1.5)) # Human pause to select menu
                        
                        # Feed the image path to file input element
                        file_input = find_element_with_fallbacks(driver, XPATHS_FILE_INPUT, timeout=5)
                        file_input.send_keys(image_path)
                        time.sleep(random.uniform(1.8, 2.8)) # Human pause for image to upload/render
                        
                        # Populate caption (if caption box is found; otherwise proceed without caption)
                        try:
                            caption_box = find_element_with_fallbacks(driver, XPATHS_CAPTION_BOX, timeout=5)
                            try:
                                caption_box.click()
                            except Exception:
                                pass
                            time.sleep(random.uniform(0.5, 1.0)) # Pause before typing
                            
                            # Safely clear the caption box using key combinations
                            try:
                                caption_box.send_keys(Keys.CONTROL, 'a')
                                caption_box.send_keys(Keys.DELETE)
                            except Exception:
                                pass
                            
                            # Paste message via clipboard for speed and emoji formatting support
                            if message_template:
                                pyperclip.copy(message_template)
                                ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
                                time.sleep(random.uniform(1.0, 2.0)) # Human pause to review caption before sending
                        except Exception as e:
                            log(f"⚠️ Caption input skipped or caption box not found: {e}. Attempting to send file anyway.")
                        
                        # Click the green Send button on media preview page
                        try:
                            click_element_with_fallbacks(driver, XPATHS_SEND_BTN, timeout=5)
                        except Exception:
                            # Fallback to sending Enter key if send button is not found
                            ActionChains(driver).send_keys(Keys.ENTER).perform()
                            
                        time.sleep(random.uniform(1.5, 2.5)) # Human pause for media send to register
                        log(f"✅ Image with template sent to {number}")
                        messages_sent += 1
                    except Exception as e:
                        log(f"⚠️ Failed to send image to {number}: {e}")
                        messages_skipped += 1
                        # Attempt to cancel upload modal using escape key
                        try:
                            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                        except Exception:
                            pass
                else:
                    try:
                        msg_box = find_element_with_fallbacks(driver, XPATHS_TEXT_BOX, timeout=5)
                        try:
                            msg_box.click()
                        except Exception:
                            pass
                        time.sleep(random.uniform(0.8, 1.5)) # Human pause before pasting
                        
                        # Safely clear text box using key combinations
                        try:
                            msg_box.send_keys(Keys.CONTROL, 'a')
                            msg_box.send_keys(Keys.DELETE)
                        except Exception:
                            pass
                        
                        # Paste message via clipboard (preserves newlines and emojis, types 10x faster)
                        pyperclip.copy(message_template)
                        ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
                        time.sleep(random.uniform(1.0, 2.0)) # Human pause to review text before sending
                        
                        # Click send button
                        try:
                            click_element_with_fallbacks(driver, XPATHS_SEND_BTN, timeout=5)
                        except Exception:
                            # Fallback to sending Enter key if send button click fails
                            ActionChains(driver).send_keys(Keys.ENTER).perform()
                            
                        time.sleep(random.uniform(1.0, 2.0)) # Human pause after send
                        log(f"✅ Text message sent to {number}")
                        messages_sent += 1
                    except Exception as e:
                        log(f"⚠️ Failed to send text message to {number}: {e}")
                        messages_skipped += 1

                # Update progress indicators
                update_stats_bar(total_numbers_loaded, messages_sent, messages_skipped, idx + 1)

            except Exception as e:
                log(f"⚠️ General error processing contact {number}: {e}")
                messages_skipped += 1
                update_stats_bar(total_numbers_loaded, messages_sent, messages_skipped, idx + 1)
                clear_search_bar(driver)

            # Random cool-off between consecutive numbers
            if idx < len(numbers) - 1 and not stop_event.is_set():
                delay = random.uniform(2.5, 6.0)
                log(f"Sleeping for {delay:.1f}s to mimic human behavior...")
                time.sleep(delay)

            # Periodic long sleep cycles to prevent account flagging
            if (idx + 1) % 20 == 0 and idx < len(numbers) - 1 and not stop_event.is_set():
                cooldown = random.uniform(50.0, 95.0)
                log(f"💤 Taking a protective anti-ban pause for {cooldown:.1f} seconds after 20 messages...")
                time.sleep(cooldown)

        log("🏁 Execution cycle finished.")
    finally:
        gui_queue.put(('reset_buttons', None))

def start_sending_thread():
    global numbers, message_template
    # Read and capture inputs from widgets on main thread to avoid GUI thread-safety issues
    message_template = message_entry.get("1.0", tk.END).strip()
    
    manual_raw = number_entry.get("1.0", tk.END).strip().split("\n")
    cleaned_manual = [num.strip() for num in manual_raw if num.strip()]
    
    # Merge, filter empty, and remove duplicates
    numbers = list(dict.fromkeys(cleaned_manual + numbers))
    
    if not numbers:
        messagebox.showwarning("Empty Numbers List", "Please load numbers from a CSV or enter them manually (one per line).")
        return
        
    btn_send_messages.config(state=tk.DISABLED, bg="#9ca3af")
    btn_stop_sending.config(state=tk.NORMAL, bg="#ef4444")
    
    worker = threading.Thread(target=send_messages_worker)
    worker.daemon = True
    worker.start()

# --- Thread-safe GUI Queue Processor ---
def process_queue():
    try:
        while True:
            action, data = gui_queue.get_nowait()
            if action == 'log':
                log_area.insert(tk.END, data + "\n")
                log_area.see(tk.END)
            elif action == 'stats':
                total, sent, skipped, progress = data
                stats_text = f"Total: {total}   |   Sent: {sent}   |   Skipped: {skipped}   |   Progress: {progress} / {total}"
                stats_var.set(stats_text)
            elif action == 'enable_remove_image':
                btn_remove_image.config(state=tk.NORMAL, bg="#4b5563")
            elif action == 'disable_remove_image':
                btn_remove_image.config(state=tk.DISABLED, bg="#d1d5db")
            elif action == 'enable_open_whatsapp':
                btn_open_whatsapp.config(state=tk.NORMAL, bg="#00a884")
            elif action == 'reset_buttons':
                btn_send_messages.config(state=tk.NORMAL, bg="#0ea5e9")
                btn_stop_sending.config(state=tk.DISABLED, bg="#d1d5db")
            gui_queue.task_done()
    except queue.Empty:
        pass
    root.after(100, process_queue)

# --- Button Hover Color Effects for Premium Feel ---
def bind_hover(widget, hover_bg, normal_bg):
    widget.bind("<Enter>", lambda e: widget.config(bg=hover_bg) if widget['state'] != 'disabled' else None)
    widget.bind("<Leave>", lambda e: widget.config(bg=normal_bg) if widget['state'] != 'disabled' else None)

# --- GUI Construction ---
root = tk.Tk()
root.title("📨 WhatsApp Bulk Dispatcher (Production Edition)")
root.geometry("720x780")
root.minsize(650, 700)
root.configure(bg="#f0f2f5") # Classic WhatsApp Web ambient background grey

# Styles
font_normal = ("Segoe UI", 10)
font_bold = ("Segoe UI", 10, "bold")
font_header = ("Segoe UI", 11, "bold")
font_console = ("Consolas", 9)

# 1. Action Control Bar
frame_controls = tk.LabelFrame(root, text=" ⚡ Operation Controls ", font=font_header, bg="#ffffff", fg="#008069", bd=1, relief="solid")
frame_controls.pack(pady=10, padx=12, fill="x")
frame_controls.grid_columnconfigure(0, weight=1)
frame_controls.grid_columnconfigure(1, weight=1)
frame_controls.grid_columnconfigure(2, weight=1)

btn_open_whatsapp = tk.Button(frame_controls, text="🌐 Open WhatsApp Web", command=open_whatsapp, bg="#00a884", fg="#ffffff", font=font_bold, relief="flat", activebackground="#008069", activeforeground="#ffffff", cursor="hand2")
btn_open_whatsapp.grid(row=0, column=0, padx=10, pady=12, sticky="ew", ipady=6)
bind_hover(btn_open_whatsapp, "#008069", "#00a884")

btn_send_messages = tk.Button(frame_controls, text="📤 Start Dispatch", command=start_sending_thread, bg="#0ea5e9", fg="#ffffff", font=font_bold, relief="flat", activebackground="#0284c7", activeforeground="#ffffff", cursor="hand2")
btn_send_messages.grid(row=0, column=1, padx=10, pady=12, sticky="ew", ipady=6)
bind_hover(btn_send_messages, "#0284c7", "#0ea5e9")

btn_stop_sending = tk.Button(frame_controls, text="⏹️ Stop", command=stop_sending, bg="#d1d5db", fg="#ffffff", font=font_bold, relief="flat", activebackground="#dc2626", activeforeground="#ffffff", cursor="hand2", state=tk.DISABLED)
btn_stop_sending.grid(row=0, column=2, padx=10, pady=12, sticky="ew", ipady=6)
bind_hover(btn_stop_sending, "#dc2626", "#d1d5db")

# 2. File and CSV Actions Card
frame_files = tk.LabelFrame(root, text=" 📂 Media & Contacts Upload ", font=font_header, bg="#ffffff", fg="#008069", bd=1, relief="solid")
frame_files.pack(pady=5, padx=12, fill="x")
frame_files.grid_columnconfigure(0, weight=1)
frame_files.grid_columnconfigure(1, weight=1)
frame_files.grid_columnconfigure(2, weight=1)

btn_select_image = tk.Button(frame_files, text="🖼️ Select Image (Optional)", command=select_image, bg="#f59e0b", fg="#ffffff", font=font_bold, relief="flat", activebackground="#d97706", activeforeground="#ffffff", cursor="hand2")
btn_select_image.grid(row=0, column=0, padx=10, pady=12, sticky="ew", ipady=5)
bind_hover(btn_select_image, "#d97706", "#f59e0b")

btn_remove_image = tk.Button(frame_files, text="Remove Image", command=remove_image, bg="#d1d5db", fg="#ffffff", font=font_bold, relief="flat", cursor="hand2", state=tk.DISABLED)
btn_remove_image.grid(row=0, column=1, padx=10, pady=12, sticky="ew", ipady=5)
bind_hover(btn_remove_image, "#9ca3af", "#d1d5db")

btn_upload_csv = tk.Button(frame_files, text="📊 Load CSV Contacts", command=load_csv, bg="#10b981", fg="#ffffff", font=font_bold, relief="flat", activebackground="#059669", activeforeground="#ffffff", cursor="hand2")
btn_upload_csv.grid(row=0, column=2, padx=10, pady=12, sticky="ew", ipady=5)
bind_hover(btn_upload_csv, "#059669", "#10b981")

# 3. Input Panels Frame
frame_inputs = tk.Frame(root, bg="#f0f2f5")
frame_inputs.pack(pady=5, padx=12, fill="both", expand=False)
frame_inputs.grid_columnconfigure(0, weight=1)
frame_inputs.grid_columnconfigure(1, weight=1)

# Numbers Column
frame_num_col = tk.LabelFrame(frame_inputs, text=" 📱 Recipient Numbers ", font=font_header, bg="#ffffff", fg="#008069", bd=1, relief="solid")
frame_num_col.grid(row=0, column=0, padx=(0, 6), pady=5, sticky="nsew")
lbl_sub_num = tk.Label(frame_num_col, text="Enter manually (one per line) or load CSV:", font=("Segoe UI", 8), bg="#ffffff", fg="#667781")
lbl_sub_num.pack(anchor="w", padx=8, pady=(4, 0))
number_entry = scrolledtext.ScrolledText(frame_num_col, height=7, font=font_normal, bd=1, relief="solid", highlightthickness=0)
number_entry.pack(padx=8, pady=8, fill="both", expand=True)

# Message Template Column
frame_msg_col = tk.LabelFrame(frame_inputs, text=" 💬 Message Template ", font=font_header, bg="#ffffff", fg="#008069", bd=1, relief="solid")
frame_msg_col.grid(row=0, column=1, padx=(6, 0), pady=5, sticky="nsew")
lbl_sub_msg = tk.Label(frame_msg_col, text="Type text. E.g. Hey [Number] prefix is automated:", font=("Segoe UI", 8), bg="#ffffff", fg="#667781")
lbl_sub_msg.pack(anchor="w", padx=8, pady=(4, 0))
message_entry = scrolledtext.ScrolledText(frame_msg_col, height=7, font=font_normal, bd=1, relief="solid", highlightthickness=0)
message_entry.pack(padx=8, pady=8, fill="both", expand=True)

# 4. Status Bar
stats_var = tk.StringVar()
stats_var.set("Total: 0   |   Sent: 0   |   Skipped: 0   |   Progress: 0 / 0")
stats_bar = tk.Label(root, textvariable=stats_var, font=font_bold, bg="#008069", fg="#ffffff", anchor="center", relief="flat", bd=0, pady=6)
stats_bar.pack(fill="x", padx=12, pady=5)

# 5. Live Console / Log Panel
frame_log = tk.LabelFrame(root, text=" 🖥️ Live Activity Log ", font=font_header, bg="#ffffff", fg="#008069", bd=1, relief="solid")
frame_log.pack(pady=5, padx=12, fill="both", expand=True)
log_area = scrolledtext.ScrolledText(frame_log, font=font_console, bg="#111827", fg="#10b981", bd=0, insertbackground="white", highlightthickness=0)
log_area.pack(padx=6, pady=6, fill="both", expand=True)

# Start Tkinter thread queue processing
root.after(100, process_queue)

if __name__ == '__main__':
    root.mainloop()
