# This is a sample Python script.

# Press Shift+F10 to execute it or replace it with your code.
# Press Double Shift to search everywhere for classes, files, tool windows, actions, and settings.
from time import sleep
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions

def test():
    chrome_options = Options()
    chrome_options.add_argument("--incognito")
    browser = webdriver.Chrome()
    # browser.implicitly_wait()
    browser.get("https://instagram.com")

    wait = WebDriverWait(browser, 10)
    element = wait.until(
        expected_conditions.presence_of_element_located((By.NAME, "username"))
    )

    username_textbox = browser.find_element_by_xpath("//input[@name='username']")
    username_textbox.click()
    username_textbox.clear()
    username_textbox.send_keys("Apple")

    password_textbox = browser.find_element_by_xpath("//input[@name='password']")
    password_textbox.click()
    password_textbox.clear()
    password_textbox.send_keys("Apple")

    wait.until(
        expected_conditions.presence_of_element_located((By.XPATH, "//button[normalize-space()='Log In']"))
    )

    login_link = browser.find_element_by_xpath("//button[normalize-space()='Log In']")
    login_link.click()
    sleep(5)
    browser.close()


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    test()
# See PyCharm help at https://www.jetbrains.com/help/pycharm/
