import argparse
import copy
import json
import os
import re
import socket
import sys
import urllib.parse
import urllib.request
from json.decoder import JSONDecodeError
from pathlib import Path
from time import sleep
from colorama import Fore, init
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from fake_useragent import UserAgent
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from seleniumwire import webdriver
from webdriver_manager.chrome import ChromeDriverManager

raw_date_format = "%Y-%m-%dT%H:%M:%S.000Z"
clean_date_format = "%Y-%m-%d %H:%M:%S"

# Hash codes used in API queries by Instagram. These are needed to generate API calls to scrape media
TIMELINE_HASH = '32b14723a678bd4628d70c1f877b94c9'
IGTV_HASH = 'bc78b344a68ed16dd5d7f264681c4c76'


# Metadata used to remember how far back we need to look for new content
def subject_builder(name, date, igtv_last, image_count, video_count, igtv_count, timeline_size, igtv_size, index):
    new_subject = {
        'name'         : name,
        'date-last'    : date,
        'igtv-last'    : igtv_last,
        'image-count'  : image_count,
        'video-count'  : video_count,
        'igtv-count'   : igtv_count,
        "timeline-size": timeline_size,
        'igtv-size'    : igtv_size,
        'index'        : index
    }
    return new_subject


def internet():
    ip = socket.gethostbyname(socket.gethostname())
    # print(ip)
    if ip == "127.0.0.1":
        print("No internet!")
        return False
    else:
        print("Internet connection detected!")
        return True


# Automated login process form Instagram's index page
def login(browser, email, password):
    browser.get("https://instagram.com")
    wait = WebDriverWait(browser, 60)
    username_textbox = wait.until(
        expected_conditions.presence_of_element_located((By.XPATH, "//input[@name='username']")))
    username_textbox.click()
    username_textbox.clear()
    username_textbox.send_keys(email)

    password_textbox = browser.find_element_by_xpath("//input[@name='password']")
    password_textbox.click()
    password_textbox.clear()
    password_textbox.send_keys(password)

    login_link = wait.until(
        expected_conditions.presence_of_element_located((By.XPATH, "//button[normalize-space()='Log In']"))
    )
    login_link.click()

    try:
        home = wait.until(expected_conditions.presence_of_element_located((By.XPATH, "//a[@href='/']")))
    except TimeoutException:
        login_error = browser.find_element_by_xpath("//p[@data-testid='login-error-message']")
        print(f"Login failed!\nInstagram says: {login_error.text}")
        raise SystemExit

    home.click()
    sleep(3)

    not_now = wait.until(
        expected_conditions.presence_of_element_located((By.XPATH, "//button[normalize-space()='Not Now']")))
    not_now.click()


# Visits the target profile's '?__a=1' API endpoint to scrape the JSON response.
# The response has all of the metadata needed to begin scraping the rest of the profile.
def make_initial_request(browser, profile_name):
    response = None
    del browser.requests
    browser.get(f"https://instagram.com/{profile_name}/?__a=1")
    requests = browser.requests
    for request in reversed(requests):
        if request.path == f"/{profile_name}/":
            response = request.response
            break
    return response


# Extract relevant metadata used to scrape media from an account.
# Returns:
#   subject_id:     The target's unique identifier for Instagram's API
#   timeline_size:  The total number of posts on the target's timeline. Note: not the total number of images/videos
#   igtv_size:      The total number of videos in the target's IGTV library
#   has_next:       Whether or not the target's timeline has another page of content to scrape
#   end_cursor:     The cursor code for where the next page of content starts in the API
#   user:           The JSON content, in dict form, for the target's info and the first 12 posts of both their timeline and IGTV
#
def get_meta_data(response):
    if response is None:
        return None
    body = response.body.decode('utf-8')
    metadata = json.loads(body)
    timeline_size = metadata['graphql']['user']['edge_owner_to_timeline_media']['count']
    igtv_size = metadata['graphql']['user']['edge_felix_video_timeline']['count']
    subject_id = metadata['graphql']['user']['id']
    has_next = metadata['graphql']['user']['edge_owner_to_timeline_media']['page_info']['has_next_page']
    end_cursor = metadata['graphql']['user']['edge_owner_to_timeline_media']['page_info']['end_cursor']
    try:
        user = metadata['graphql']['user']
    except KeyError:
        user = metadata['data']['user']

    return subject_id, timeline_size, igtv_size, has_next, end_cursor, user


# Extracts all media from posts made on an account, or all media from posts made since last scraping, depending on user input
# Browser:      Web driver instance used to automate the browser
# profile_name: Target's username
# data:         The user's data including login, and metadata for previously scraped profiles
# force:        Used to determine if program should scrape entire profile.
#                   When False, only the posts made since last scraping will be scraped, Otherwise scrape everything
def scrape(browser, profile_name, data, force=False):
    urls = [[[], []], []]
    # Set these defaults in case we're scraping a profile that's new to the program
    img_count = 0
    vid_count = 0
    igtv_count = 0
    timeline_constraint = 0
    igtv_constraint = 0
    subject_index = None
    data_subjects = data['subjects']
    # Check if target is amongst scraped profiles. If so, read metadata into memory
    for subject in data_subjects:
        if subject['name'] == profile_name.casefold():  # if target is among previously scraped
            img_count = subject['image-count']
            vid_count = subject['video-count']
            igtv_count = subject['igtv-count']
            timeline_constraint = subject['date-last']
            igtv_constraint = subject['igtv-last']
            subject_index = subject['index']
            break

    response = make_initial_request(browser, profile_name)
    subject_id, timeline_size, igtv_size, has_next_page, end_cursor, user = get_meta_data(response)

    # Scrape media posts made since last scraping.
    if not force:
        new_timeline_constraint = scrape_timeline(browser, user, urls, timeline_constraint)
        new_igtv_constraint = scrape_igtv(browser, user, urls, igtv_constraint)
    # User forced scrape of entire profile
    else:
        new_timeline_constraint = scrape_timeline(browser, user, urls, 0)
        new_igtv_constraint = scrape_igtv(browser, user, urls, 0)

    # If the target account has made no posts since the last scraping
    if new_timeline_constraint == 0 and new_igtv_constraint == 0:
        print(f"No new content on {profile_name}'s timeline or IGTV feed")
        return
    if new_timeline_constraint == 0:
        new_timeline_constraint = timeline_constraint
    if new_igtv_constraint == 0:
        new_igtv_constraint = igtv_constraint

    new_image_count = img_count + len(urls[0][0])
    new_video_count = vid_count + len(urls[0][1])
    new_igtv_count = igtv_count + len(urls[1])

    # Ensure profile name is safe for File I/O
    safe_profile_name = re.sub('[^a-zA-Z0-9_]', '_', profile_name)

    img_path = os.path.join(os.getcwd(), safe_profile_name, "timeline", "pictures")
    vid_path = os.path.join(os.getcwd(), safe_profile_name, "timeline", "videos")
    igtv_path = os.path.join(os.getcwd(), safe_profile_name, "igtv")

    Path(img_path).mkdir(parents=True, exist_ok=True)
    Path(vid_path).mkdir(exist_ok=True)
    Path(igtv_path).mkdir(exist_ok=True)

    # Save all scraped urls to disk
    count = new_image_count
    print(f"Saving {len(urls[0][0])} timeline images...")
    for url in urls[0][0]:
        file_name = "{0}/{1}/timeline/pictures/{2}_{3}.png".format(os.getcwd(), safe_profile_name, safe_profile_name,
                                                                   count)
        print(file_name)
        urllib.request.urlretrieve(url, file_name)
        count -= 1

    count = new_video_count
    print(f"Saving {len(urls[0][1])} timeline videos... ")
    for url in urls[0][1]:
        file_name = "{0}/{1}/timeline/videos/{2}_{3}.mp4".format(os.getcwd(), safe_profile_name, safe_profile_name,
                                                                 count)
        print(file_name)
        urllib.request.urlretrieve(url, file_name)
        count -= 1

    count = new_igtv_count
    print(f"Saving {len(urls[1])} IGTV videos...")
    for url in urls[1]:
        file_name = "{0}/{1}/igtv/{2}_igtv_{3}.mp4".format(os.getcwd(), safe_profile_name, safe_profile_name, count)
        print(file_name)
        urllib.request.urlretrieve(url, file_name)
        count -= 1

    # Update JSON file to reflect changes to target's metadata
    if subject_index is None:
        subject_index = len(data_subjects)
        data['subjects'].append({})

    new_subject = subject_builder(
        profile_name.casefold(),
        new_timeline_constraint,
        new_igtv_constraint,
        new_image_count,
        new_video_count,
        new_igtv_count,
        timeline_size,
        igtv_size,
        subject_index
    )

    data['subjects'][subject_index] = new_subject
    with open("user.json", "w") as file:
        json.dump(data, file)


# Scrapes all the media from IGTV posts made after the time_constraint
# browser:          webdriver used to automate browser
# user_dict:        dict of the JSON response containing IGTV data, among other things
# urls:             list to place media URLs into
# time_constraint:  the most recent date for the previously scraped IGTV media. Used to avoid redownloading data unnecessarily
#
# returns:           the date for the most recent IGTV currently being scraped. Will always be 0 the first time a profile is scraped
def scrape_igtv(browser, user_dict, urls, time_constraint):
    print("scraping igtv")
    new_igtv_constraint = 0
    user = copy.deepcopy(user_dict)
    USER_ID = user['id']
    flag = True
    has_next = True
    while has_next:
        sleep(3)
        has_next = user['edge_felix_video_timeline']['page_info']['has_next_page']
        temp, need_next = get_igtv_links(user['edge_felix_video_timeline']['edges'], urls, time_constraint,
                                         is_date_needed=flag)
        new_igtv_constraint = max(new_igtv_constraint, temp)
        if not need_next or not has_next:
            shortcodes_to_links(browser, urls[1])
            return new_igtv_constraint
        url = build_request(USER_ID, user['edge_felix_video_timeline']['page_info']['end_cursor'], IGTV_HASH)
        del browser.requests
        browser.get(url)
        for request in reversed(browser.requests):
            if request.path == "/graphql/query/":
                response = request.response
                body = json.loads(response.body.decode('utf-8'))
                user = body['data']['user']
                break
        flag = False
    return new_igtv_constraint


# Gets shortcodes for an IGTV post.
# Shortcodes are used to obfuscate the actual URL to the media.
# Shortcodes are still scraped so that the program can convert them to the actual URLs later on
#
# edges:            A dict of IGTV post data
# urls:             List to store URLs
# time_constraint:  Timestamp of the most recently IGTV post that was previously scraped
# is_date_needed:   Whether or not the program needs the post's timestamp.
#
# returns:          Timestamp of the most recent IGTV post that is currently being scraped
def get_igtv_links(edges, urls, time_constraint, is_date_needed=True):
    most_recent_timestamp = 0
    for edge in edges:
        if edge['node']['taken_at_timestamp'] <= time_constraint:
            return most_recent_timestamp, False
        if is_date_needed:
            most_recent_timestamp = edge['node']['taken_at_timestamp']
            is_date_needed = False
        if edge['node']['product_type'] == "igtv":
            urls[1].append(edge['node']['shortcode'])
    return most_recent_timestamp, True


# uses previously scraped shortcodes to visit each post and scrape the direct links to its corresponding media
#
# browser:  webdriver used to automate browser
# urls:     List to store URLs
def shortcodes_to_links(browser, urls):
    for index, shortcode in enumerate(urls):
        browser.get(f"https://www.instagram.com/tv/{shortcode}/")
        sleep(2)
        wait = WebDriverWait(browser, 60)
        # feels a bit hacky, but for whatever reason, this xpath is the only one that would locate the video
        video_element = wait.until(expected_conditions.presence_of_element_located(
            (By.XPATH, '//*[@id="react-root"]/section/main/div/div[1]/article/div[2]/div/div/div/div/div/video')))
        src = video_element.get_attribute("src")
        urls[index] = src


# Scrapes all the media from the target's timeline made after the time_constraint
# browser:          webdriver used to automate browser
# user_dict:        dict of the JSON response containing timeline data, among other things
# urls:             list to place media URLs into
# time_constraint:  the most recent date for the previously scraped timeline media. Used to avoid redownloading data unnecessarily
#
# returns:           the date for the most recent timeline post being scraped. Will always be 0 the first time a profile is scraped
def scrape_timeline(browser, user_dict, urls, time_constraint):
    print("scraping timeline")
    new_timeline_constraint = 0
    user = copy.deepcopy(user_dict)
    user_id = user['id']
    flag = True
    has_next = True
    while has_next:
        sleep(3)
        has_next = user['edge_owner_to_timeline_media']['page_info']['has_next_page']
        temp, need_next = get_timeline_links(user['edge_owner_to_timeline_media']['edges'], urls,
                                             time_constraint, is_date_needed=flag)
        new_timeline_constraint = max(new_timeline_constraint, temp)
        if not need_next or not has_next:
            return new_timeline_constraint
        url = build_request(user_id, user['edge_owner_to_timeline_media']['page_info']['end_cursor'], TIMELINE_HASH)
        del browser.requests
        browser.get(url)
        for request in reversed(browser.requests):
            if request.path == f"/graphql/query/":
                response = request.response
                body = json.loads(response.body.decode('utf-8'))
                user = body['data']['user']
                break
        flag = False
    return new_timeline_constraint


# Gets URLs for timeline posts.
#
# edges:            A dict of timeline post data
# urls:             List to store URLs
# time_constraint:  Timestamp of the most recently timeline post that was previously scraped
# is_date_needed:   Whether or not the program needs the post's timestamp.
#
# returns:          Timestamp of the most recent timeline post that is currently being scraped
def get_timeline_links(edges, urls, time_constraint, is_date_needed=True):
    most_recent_timestamp = 0
    for edge in edges:
        if edge['node']['taken_at_timestamp'] <= time_constraint:
            return most_recent_timestamp, False
        if is_date_needed:
            most_recent_timestamp = edge['node']['taken_at_timestamp']
            is_date_needed = False
        if edge['node']['__typename'] == "GraphImage":
            urls[0][0].append(edge['node']['display_url'])
        elif edge['node']['__typename'] == "GraphVideo":
            urls[0][1].append(edge['node']['video_url'])
        # a typename of GraphSidecar indicates the post has multiple images or videos
        elif edge['node']['__typename'] == "GraphSidecar":
            children = edge['node']['edge_sidecar_to_children']
            for child_edge in children['edges']:
                if child_edge['node']['__typename'] == "GraphImage":
                    urls[0][0].append(child_edge['node']['display_url'])
                elif child_edge['node']['__typename'] == "GraphVideo":
                    urls[0][1].append(child_edge['node']['video_url'])
    return most_recent_timestamp, True


# builds request to fetch a page from Facebook's GraphQL
# Used to iterate through an Instagram's content via the API rather than visiting each post manually
#
# user_id:      ID of the target profile
# end_cursor:   The cursor code for where the next page of content starts in the API
# query_hash:   The hash code for the query. Timeline and IGTV posts use different codes.
def build_request(user_id, end_cursor, query_hash):
    if end_cursor is None:
        raise ValueError
    meta = {
        'id'   : user_id,
        'first': 30,
        'after': end_cursor
    }
    parameters = {
        'query_hash': query_hash,
        'variables' : json.dumps(meta)
    }
    return "https://www.instagram.com/graphql/query/?" + urllib.parse.urlencode(parameters)


def build_default_user():
    user_dict = {
        "email"   : "",
        "pass"    : "",
        "subjects": []
    }
    return user_dict


# Ask user for the login for their Instagram profile
#
# returns:
#   username
#   password
def request_login():
    print("There is no username/password on file!")
    # print("Gonna need your login info, chief.")
    print("Using an Instagram account in conjunction with this program mitigates Instagram's anti-bot measures."
          "\nIf you plan to use this regularly, I suggest using a throwaway account just to be safe."
          f"\nFull disclosure, your username and password is stored in {os.path.join(os.getcwd(), 'user.json')}\n")
    while True:
        username = input("Please enter your Instagram username: ")
        password = input("Okay, now your password: ")
        confirmation = input(
            f"So, your username is {username}, and your password is {password}. Is that correct? [Y/N]\n").casefold()
        if "y" == confirmation.casefold():
            break
        elif "n" == confirmation.casefold():
            print("No worries! Let's try again.")
        else:
            print("That was a Y or N question. Let's try again.")
    return username, password


def main():
    init(autoreset=True)
    user_file_path = os.path.join(os.getcwd(), "user.json")
    if not os.path.exists(user_file_path):
        with open(user_file_path, "w") as file:
            username, authentication = request_login()
            user_info = build_default_user()
            user_info['email'] = username
            user_info['pass'] = authentication
            json.dump(user_info, file)
    try:
        with open("user.json") as json_data:
            user_data = json.load(json_data)
    except JSONDecodeError:
        print(Fore.RED + 'User JSON corrupted! Rebuilding...')
        # with open("user.json") as json_data:
        new_json = open("user.json", "w")
        user_data = build_default_user()
        json.dump(user_data, new_json)

    email = user_data.get("email")
    password = user_data.get("pass")
    subjects = user_data['subjects']

    # CLI parser for quick testing
    # parser = argparse.ArgumentParser()
    # mutex_group = parser.add_mutually_exclusive_group()
    # login_group = parser.add_argument_group()
    #
    # mutex_group.add_argument('-t', '--target',
    #                     type=str,
    #                     help='Username of Instagram profile you\'d like to scrape'
    #                     )
    # login_group.add_argument('-u', '--username',
    #                     type=str,
    #                     help='email of your Instagram account'
    #                     )
    # login_group.add_argument('-p', '--password',
    #                     type=str,
    #                     help='password of your Instagram account'
    #                     )
    # parser.add_argument('-f', '--force',
    #                     default=False,
    #                     const=True,
    #                     # type=bool,
    #                     action='store_const',
    #                     help='Force program to re-download the entirety of an account\'s media. '
    #                     )
    # mutex_group.add_argument('-r', '--reset',
    #                     default=False,
    #                     const=True,
    #                     # type=bool,
    #                     action='store_const',
    #                     help='Deletes login information and all data stored about previous scrapings. '
    #                          'Previously scraped media will not be deleted.'
    #                     )
    # names = []
    # for subject in subjects:
    #     names.append(subject['name'])
    # mutex_group.add_argument('--delete',
    #                     type=str,
    #                     choices=names,
    #                     help='Use to delete data about previously scraped profile. '
    #                          'Data is used to avoid downloading media that the program has previously downloaded.'
    #                     )
    #
    # arguments = parser.parse_args()
    # string = arguments.__str__()
    # print(string)

    internet()
    print('launching browser...')
    regex = re.compile('[^a-zA-Z0-9_.]')
    # if arguments.target is None:
    target = input("Enter username of the account you would like to scrape: ").strip()

    chrome_options = webdriver.ChromeOptions()
    # chrome_options.add_argument("--incognito")
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument("window-size=1920,1080")
    chrome_options.add_argument(f"user-agent='{UserAgent().random}'")
    # chrome_options.add_argument("--headless")
    browser = webdriver.Chrome(ChromeDriverManager().install(), options=chrome_options)
    browser.scopes = ['.*instagram.']

    # if arguments.username is not None and arguments.passworn is not None:
    #     try:
    #         login(browser, arguments.username, arguments.password)
    #     except SystemExit:
    #         browser.quit()
    #         print('Exiting Program')
    #         return 1
    if email != '' and password != '':
        try:
            login(browser, email, password)
        except SystemExit:
            browser.quit()
            print('Exiting program...')
            return 1

    if regex.search(target) is None:
        scrape(browser, target, json.load(open("user.json")))
    else:
        print(f"{target} is not a valid Instagram username! Usernames can only consist of letters, numbers, "
              f"underscores, and periods.")
        browser.quit()
        return 1
    print("Closing browser...")
    browser.quit()
    input("Scrape completed! Enter any key to close the program")


if __name__ == '__main__':
    sys.exit(main())
