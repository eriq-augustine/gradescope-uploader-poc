#!/usr/bin/env python3

import json
import os
import subprocess
import time

import bs4
import requests

# TEST - IDS
COURSE_ID = '672346'
ASSIGNMENT_ID = '3847684'

URL_BASE = 'https://www.gradescope.com'
URL_LOGIN = 'https://www.gradescope.com/login'
URL_EDIT_OUTLINE = 'https://www.gradescope.com/courses/%s/assignments/%s/outline/edit' % (COURSE_ID, ASSIGNMENT_ID)
URL_PATCH_OUTLINE = 'https://www.gradescope.com/courses/%s/assignments/%s/outline' % (COURSE_ID, ASSIGNMENT_ID)

SECRETS_PATH = 'secrets.json'
QUIZ_TEX_PATH = 'quiz.tex'
QUIZ_PDF_PATH = 'quiz.pdf'
POS_PATH = 'quiz.pos'

COMPILE_SCRIPT = 'compile-latex.sh'

SP_PER_PT = 65536

def main():
    email, password = load_secrets()

    compile_tex()

    boxes = get_bounding_boxes()
    upload(email, password, boxes)

def load_secrets():
    if (not os.path.isfile(SECRETS_PATH)):
        raise ValueError("Secrets path '%s' does not exist or is not a file." % (SECRETS_PATH))

    with open(SECRETS_PATH, 'r') as file:
        data = json.load(file)

    for key in ['email', 'pass']:
        if (key not in data):
            raise ValueError("Key '%s' missing from secrets." % (key))

    return data['email'], data['pass']

def compile_tex():
    result = subprocess.run(['bash', COMPILE_SCRIPT, QUIZ_TEX_PATH], capture_output = True)
    if (result.returncode != 0):
        raise ValueError("Quiz did not compile. Stdout: '%s', Stderr: '%s'" % (result.stdout, result.stderr))

def get_bounding_boxes():
    boxes = {}

    with open(POS_PATH, 'r') as file:
        for line in file:
            line = line.strip()
            if (line == ""):
                continue

            parts = [part.strip() for part in line.split(',')]
            if (len(parts) != 11):
                raise ValueError("Position file has row with bad number of parts. Expecting 11, found %d." % (len(parts)))

            # "ll" == "lower-left"
            # "ur" == "upper-right"
            (index, subid, content_type, page_number, ll_x, ll_y, ur_x, ur_y, page_width, page_height, origin) = parts

            if (origin != 'bottom-left'):
                raise ValueError("Unknown bounding box origin: '%s'." % (origin))

            index = int(index)
            page_number = int(page_number)

            if (content_type not in ['mcq', 'ma']):
                raise ValueError("Unknown content type: '%s'." % (content_type))

            ll_x = float(ll_x.removesuffix('sp'))
            ll_y = float(ll_y.removesuffix('sp'))
            ur_x = float(ur_x.removesuffix('sp'))
            ur_y = float(ur_y.removesuffix('sp'))

            page_width = float(page_width.removesuffix('pt')) * SP_PER_PT
            page_height = float(page_height.removesuffix('pt')) * SP_PER_PT

            # Origin is upper-left, point 1 is upper-left, point 2 is lower-right.
            x1 = round(100.0 * (ll_x / page_width), 1)
            y1 = round(100.0 * (1.0 - (ur_y / page_height)), 1)
            x2 = round(100.0 * (ur_x / page_width), 1)
            y2 = round(100.0 * (1.0 - (ll_y / page_height)), 1)

            # If there is an existing box, extend it.
            if (index in boxes):
                old_x1 = boxes[index]['x1']
                old_y1 = boxes[index]['y1']
                old_x2 = boxes[index]['x2']
                old_y2 = boxes[index]['y2']

                old_page = boxes[index]['page_number']
                if (old_page != page_number):
                    raise ValueError("Question %d has bounding boxes that span pages." % (index))

                x1 = min(x1, old_x1)
                y1 = min(y1, old_y1)
                x2 = max(x2, old_x2)
                y2 = max(y2, old_y2)

            boxes[index] = {
                # Note that the position file and GradeScope use 1-indexed pages.
                'page_number': page_number,
                'x1': x1,
                'y1': y1,
                'x2': x2,
                'y2': y2,
            }

    return boxes

def create_outline(bounding_boxes):
    outline = {
        "assignment": {
            "identification_regions": {
                "name": None,
                "sid": None
            }
        },
        "question_data": [
            {
                "title": "MCQ",
                "weight": 5
            },
            {
                "title": "MA",
                "weight": 5
            }
        ]
    }

    for (index, box) in bounding_boxes.items():
        outline['question_data'][index]['crop_rect_list'] = [box]

    return outline

def upload(email, password, bounding_boxes):
    outline = create_outline(bounding_boxes)
    print(json.dumps(outline, indent = 4))

    session = requests.Session()

    # Get login auth token.
    response = session.get(URL_BASE)
    response.raise_for_status()
    print("Got login page.")
    time.sleep(0.5)

    document = bs4.BeautifulSoup(response.text, 'html.parser')

    auth_input = document.select('form[action="/login"] input[name="authenticity_token"]')
    if (len(auth_input) != 1):
        raise ValueError("Did not find exactly one authentication token input, found %d." % (len(auth_input)))
    auth_input = auth_input[0]

    token = auth_input.get('value')

    data = {
        "utf8": "✓",
        "session[email]": email,
        "session[password]": password,
        "session[remember_me]": 0,
        "commit": "Log+In",
        "session[remember_me_sso]": 0,
        "authenticity_token": token,
    }

    # Login.
    response = session.post(URL_LOGIN, params = data)
    response.raise_for_status()
    print("Logged in.")
    time.sleep(0.5)

    # Get outline submission csrf token.
    response = session.get(URL_EDIT_OUTLINE)
    response.raise_for_status()
    print("Got outline edit page.")
    time.sleep(0.5)

    document = bs4.BeautifulSoup(response.text, 'html.parser')

    meta_tag = document.select('meta[name="csrf-token"]')
    if (len(meta_tag) != 1):
        raise ValueError("Did not find exactly one CSRF meta tag, found %d." % (len(meta_tag)))
    meta_tag = meta_tag[0]

    headers = {
        'Content-Type': 'application/json',
        'x-csrf-token': meta_tag.get('content'),
    }

    response = session.patch(URL_PATCH_OUTLINE,
        data = json.dumps(outline, separators = (',', ':')),
        headers = headers,
    )
    response.raise_for_status()
    print("Submitted outline.")
    # time.sleep(0.5)

if (__name__ == '__main__'):
    main()
