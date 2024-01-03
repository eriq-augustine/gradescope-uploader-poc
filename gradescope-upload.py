#!/usr/bin/env python3

import json
import os
import re
import subprocess
import time

import bs4
import requests

URL_HOMEPAGE = 'https://www.gradescope.com'
URL_LOGIN = 'https://www.gradescope.com/login'
URL_CREATE_ASSIGNMENT = 'https://www.gradescope.com/courses/%s/assignments'
URL_NEW_ASSIGNMENT_FORM = 'https://www.gradescope.com/courses/%s/assignments/new'
URL_EDIT_OUTLINE = 'https://www.gradescope.com/courses/%s/assignments/%s/outline/edit'
URL_PATCH_OUTLINE = 'https://www.gradescope.com/courses/%s/assignments/%s/outline'

SECRETS_PATH = 'secrets.json'
QUIZ_TEX_PATH = 'quiz.tex'
QUIZ_PDF_PATH = 'quiz.pdf'
POS_PATH = 'quiz.pos'

COMPILE_SCRIPT = 'compile-latex.sh'

SP_PER_PT = 65536

GRADESCOPE_SLEEP_TIME_SEC = 0.5

def main():
    email, password = load_secrets()

    # TEST
    course_id = '672346'
    assignment_name = 'Test - Upload'

    compile_tex()

    boxes = get_bounding_boxes()
    upload(course_id, assignment_name, email, password, boxes)

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

def upload(course_id, assignment_name, email, password, bounding_boxes):
    outline = create_outline(bounding_boxes)
    print(json.dumps(outline, indent = 4))

    session = requests.Session()

    login(session, email, password)
    print("Logged in.")

    assignment_id = create_assignment(session, course_id, assignment_name)
    print('Created assignment: ', assignment_id)

    submit_outline(session, course_id, assignment_id, outline)
    print("Submitted outline.")

def login(session, email, password):
    token = get_authenticity_token(session, URL_HOMEPAGE, action = '/login')

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
    time.sleep(GRADESCOPE_SLEEP_TIME_SEC)

def get_authenticity_token(session, url, action = None):
    response = session.get(url)
    response.raise_for_status()
    time.sleep(GRADESCOPE_SLEEP_TIME_SEC)

    document = bs4.BeautifulSoup(response.text, 'html.parser')

    form_selector = 'form'
    if (action is not None):
        form_selector = 'form[action="%s"]' % (action)

    auth_input = document.select('%s input[name="authenticity_token"]' % (form_selector))
    if (len(auth_input) != 1):
        raise ValueError("Did not find exactly one authentication token input, found %d." % (len(auth_input)))
    auth_input = auth_input[0]

    return auth_input.get('value')

def get_csrf_token(session, url):
    # Get outline submission csrf token.
    response = session.get(url)
    response.raise_for_status()
    time.sleep(GRADESCOPE_SLEEP_TIME_SEC)

    document = bs4.BeautifulSoup(response.text, 'html.parser')

    meta_tag = document.select('meta[name="csrf-token"]')
    if (len(meta_tag) != 1):
        raise ValueError("Did not find exactly one CSRF meta tag, found %d." % (len(meta_tag)))
    meta_tag = meta_tag[0]

    return meta_tag.get('content')

def create_assignment(session, course_id, assignment_name):
    form_url = URL_NEW_ASSIGNMENT_FORM % (course_id)
    create_url = URL_CREATE_ASSIGNMENT % (course_id)

    token = get_csrf_token(session, form_url)

    data = {
        'authenticity_token': token,
        'assignment[title]': assignment_name,
        'assignment[submissions_anonymized]': 0,
        'assignment[student_submission]': "false",
        'assignment[when_to_create_rubric]': 'while_grading',
    }

    files = {
        'template_pdf': (
            os.path.basename(QUIZ_PDF_PATH),
            open(QUIZ_PDF_PATH, 'rb')
        ),
    }

    response = session.post(create_url, data = data, files = files)
    response.raise_for_status()
    time.sleep(GRADESCOPE_SLEEP_TIME_SEC)

    if (len(response.history) == 0):
        raise ValueError("Failed to create assignment. Is the name ('%s') unique?" % (assignment_name))

    match = re.search(r'/assignments/(\d+)/outline/edit', response.history[0].text)
    if (match is None):
        print("--- Create Body ---\n%s\n------" % response.history[0].text)
        raise ValueError("Could not parse assignment ID from response body.")

    return match.group(1)

def submit_outline(session, course_id, assignment_id, outline):
    edit_url = URL_EDIT_OUTLINE % (course_id, assignment_id)
    patch_outline_url = URL_PATCH_OUTLINE % (course_id, assignment_id)

    csrf_token = get_csrf_token(session, edit_url)

    headers = {
        'Content-Type': 'application/json',
        'x-csrf-token': csrf_token,
    }

    response = session.patch(patch_outline_url,
        data = json.dumps(outline, separators = (',', ':')),
        headers = headers,
    )
    response.raise_for_status()

if (__name__ == '__main__'):
    main()
