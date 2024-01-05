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
URL_ASSIGNMENTS = 'https://www.gradescope.com/courses/%s/assignments'
URL_ASSIGNMENT = 'https://www.gradescope.com/courses/%s/assignments/%s'
URL_ASSIGNMENT_EDIT = 'https://www.gradescope.com/courses/%s/assignments/%s/edit'
URL_NEW_ASSIGNMENT_FORM = 'https://www.gradescope.com/courses/%s/assignments/new'
URL_EDIT_OUTLINE = 'https://www.gradescope.com/courses/%s/assignments/%s/outline/edit'
URL_PATCH_OUTLINE = 'https://www.gradescope.com/courses/%s/assignments/%s/outline'

SECRETS_PATH = 'secrets.json'
QUIZ_TEX_PATH = 'quiz.tex'
QUIZ_PDF_PATH = 'quiz.pdf'
POS_PATH = 'quiz.pos'

NAME_BOX_ID = 'name'
ID_BOX_ID = 'id'
SIGNATURE_BOX_ID = 'signature'
MANUAL_GRADING_BOX_ID = 'manual_grading'

EXTEND_BOX_QUESTION_TYPES = [
    'mcq',
    'ma',
    'mdd',
]

SPECIAL_QUESTION_TYPES = [
    NAME_BOX_ID,
    ID_BOX_ID,
    SIGNATURE_BOX_ID,
    MANUAL_GRADING_BOX_ID,
]

BOX_TYPES = EXTEND_BOX_QUESTION_TYPES + SPECIAL_QUESTION_TYPES

COMPILE_SCRIPT = 'compile-latex.sh'

SP_PER_PT = 65536

GRADESCOPE_SLEEP_TIME_SEC = 0.75

# TODO: Get from quiz config.
QUESTIONS = [
    {
        "name": "Regular Expression in Programming Languages",
        "points": 5,
    },
    {
        "name": "Regular Expression Vocabulary",
        "points": 20,
    },
    {
        "name": "Basic Regular Expressions",
        "points": 5,
    },
    {
        "name": "Passage",
        "points": 0,
    },
    {
        "name": "Passage Search",
        "points": 10,
    },
    {
        "name": "Quantifiers",
        "points": 5,
    },
    {
        "name": "General Quantification",
        "points": 5,
    },
    {
        "name": "Backreference Matching",
        "points": 10,
    },
    {
        "name": "Regex Golf",
        "points": 20,
    },
    {
        "name": "Write a Function",
        "points": 20,
    }
]

def main():
    email, password = load_secrets()

    # TODO: Get from config / args.
    course_id = '672346'
    assignment_name = 'Test - Upload'
    force = True

    compile_tex()

    boxes, special_boxes = get_bounding_boxes()
    upload(course_id, assignment_name, email, password, boxes, special_boxes, force = force)

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
    # {<quetion id>: {<part id>: box, ...}, ...}
    boxes = {}
    # {NAME_BOX_ID: box, ID_BOX_ID: box, SIGNATURE_BOX_ID: box}
    special_boxes = {}

    with open(POS_PATH, 'r') as file:
        for line in file:
            line = line.strip()
            if (line == ""):
                continue

            parts = [part.strip() for part in line.split(',')]
            if (len(parts) != 12):
                raise ValueError("Position file has row with bad number of parts. Expecting 11, found %d." % (len(parts)))

            # "ll" == "lower-left"
            # "ur" == "upper-right"
            (question_index, part_id, answer_id, question_type, page_number, ll_x, ll_y, ur_x, ur_y, page_width, page_height, origin) = parts

            if (origin != 'bottom-left'):
                raise ValueError("Unknown bounding box origin: '%s'." % (origin))

            # Note that the position file and GradeScope use 1-indexed pages.
            page_number = int(page_number)

            if (question_type not in BOX_TYPES):
                raise ValueError("Unknown content type: '%s'." % (question_type))

            extend_box_right = False
            if (question_type in EXTEND_BOX_QUESTION_TYPES):
                extend_box_right = True

            (x1, y1), (x2, y2) = _compute_box(ll_x, ll_y, ur_x, ur_y, page_width, page_height, extend_box_right = extend_box_right)

            if (question_type in SPECIAL_QUESTION_TYPES):
                # These boxes are special.
                if (question_type in special_boxes):
                    raise ValueError("Multiple %s bounding boxes found." % (question_type))

                special_boxes[question_type] = {
                    'page_number': page_number,
                    'x1': x1,
                    'y1': y1,
                    'x2': x2,
                    'y2': y2,
                }

                continue

            question_index = int(question_index)
            if (question_index not in range(len(QUESTIONS))):
                raise ValueError("Index from position file (%d) not in question question_index range." % (question_index))

            if (question_index not in boxes):
                boxes[question_index] = {}

            # If there is an existing box, extend it.
            if (part_id in boxes[question_index]):
                old_box = boxes[question_index][part_id]

                old_x1 = old_box['x1']
                old_y1 = old_box['y1']
                old_x2 = old_box['x2']
                old_y2 = old_box['y2']

                old_page = old_box['page_number']
                if (old_page != page_number):
                    raise ValueError("Question %d has bounding boxes that span pages." % (question_index))

                x1 = min(x1, old_x1)
                y1 = min(y1, old_y1)
                x2 = max(x2, old_x2)
                y2 = max(y2, old_y2)

            boxes[question_index][part_id] = {
                'page_number': page_number,
                'x1': x1,
                'y1': y1,
                'x2': x2,
                'y2': y2,
            }

    return boxes, special_boxes

def _compute_box(ll_x, ll_y, ur_x, ur_y, page_width, page_height, extend_box_right = False):
    ll_x = float(ll_x.removesuffix('sp'))
    ll_y = float(ll_y.removesuffix('sp'))
    ur_x = float(ur_x.removesuffix('sp'))
    ur_y = float(ur_y.removesuffix('sp'))

    page_width = float(page_width.removesuffix('pt')) * SP_PER_PT
    page_height = float(page_height.removesuffix('pt')) * SP_PER_PT

    # Origin is upper-left, point 1 is upper-left, point 2 is lower-right.
    x1 = round(100.0 * (ll_x / page_width), 1)
    y1 = round(100.0 * (1.0 - (ur_y / page_height)), 1)
    # The lower right x should always extend (at least) to the end of the page (to capture the answers).
    x2 = round(100.0 * (ur_x / page_width), 1)
    y2 = round(100.0 * (1.0 - (ll_y / page_height)), 1)

    if (extend_box_right):
        # In some question types, we want to extend (at least) to the end of the page (to capture the answers).
        x2 = max(95.0, x2)

    return (x1, y1), (x2, y2)

def create_outline(bounding_boxes, special_boxes):
    question_data = []
    for (question_index, parts) in bounding_boxes.items():
        if (len(parts) == 1):
            # Single-part question.
            question_data.append({
                'title': QUESTIONS[question_index]['name'],
                'weight': QUESTIONS[question_index]['points'],
                'crop_rect_list': list(parts.values()),
            })
        else:
            # Multi-part question.
            children = []
            for (part_id, box) in parts.items():
                children.append({
                    'title': "%s - %s" % (QUESTIONS[question_index]['name'], part_id),
                    'weight': round(QUESTIONS[question_index]['points'] / len(parts), 2),
                    'crop_rect_list': [box],
                })

            question_data.append({
                'title': QUESTIONS[question_index]['name'],
                'weight': QUESTIONS[question_index]['points'],
                # The top-level question just needs one of the bounding boxes.
                'crop_rect_list': [list(parts.values())[0]],
                'children': children,
            })

    name_box = None
    id_box = None

    for (question_type, box) in special_boxes.items():
        if (question_type == NAME_BOX_ID):
            name_box = special_boxes[NAME_BOX_ID]
        elif (question_type == ID_BOX_ID):
            id_box = special_boxes[ID_BOX_ID]
        else:
            question_data.append({
                'title': question_type,
                'weight': 0,
                'crop_rect_list': [box],
            })

    outline = {
        'assignment': {
            'identification_regions': {
                'name': name_box,
                'sid': id_box
            }
        },
        'question_data': question_data,
    }

    return outline

def upload(course_id, assignment_name, email, password, bounding_boxes, special_boxes, force = False):
    outline = create_outline(bounding_boxes, special_boxes)
    print(json.dumps(outline, indent = 4))

    assignment_name = assignment_name.strip()

    session = requests.Session()

    login(session, email, password)
    print('Logged in.')

    assignment_id = get_assignment_id(session, course_id, assignment_name)
    if (assignment_id is not None):
        if (not force):
            print("Assignment '%s' (%s) already exists. Skipping upload." % (assignment_name, assignment_id))
            return

        delete_assignment(session, course_id, assignment_id)
        print('Deleted assignment: ', assignment_id)

    assignment_id = create_assignment(session, course_id, assignment_name)
    print('Created assignment: ', assignment_id)

    submit_outline(session, course_id, assignment_id, outline)
    print('Submitted outline.')

def login(session, email, password):
    token = get_authenticity_token(session, URL_HOMEPAGE, action = '/login')

    data = {
        'utf8': '✓',
        'session[email]': email,
        'session[password]': password,
        'session[remember_me]': 0,
        'commit': 'Log+In',
        'session[remember_me_sso]': 0,
        'authenticity_token': token,
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

def get_assignment_id(session, course_id, assignment_name):
    url = URL_ASSIGNMENTS % (course_id)

    response = session.get(url)
    response.raise_for_status()
    time.sleep(GRADESCOPE_SLEEP_TIME_SEC)

    document = bs4.BeautifulSoup(response.text, 'html.parser')

    nodes = document.select('div[data-react-class="AssignmentsTable"]')
    if (len(nodes) != 1):
        raise ValueError("Did not find exactly one assignments table, found %d." % (len(nodes)))

    assignment_data = json.loads(nodes[0].get('data-react-props'))
    for row in assignment_data['table_data']:
        if (row['className'] != 'js-assignmentTableAssignmentRow'):
            continue

        id = row['id'].strip().removeprefix('assignment_')
        name = row['title'].strip()

        if (name == assignment_name):
            return id

    return None

def delete_assignment(session, course_id, assignment_id):
    form_url = URL_ASSIGNMENT_EDIT % (course_id, assignment_id)
    delete_url = URL_ASSIGNMENT % (course_id, assignment_id)

    token = get_csrf_token(session, form_url)

    data = {
        '_method': 'delete',
        'authenticity_token': token,
    }

    response = session.post(delete_url, data = data)
    response.raise_for_status()
    time.sleep(GRADESCOPE_SLEEP_TIME_SEC)

def create_assignment(session, course_id, assignment_name):
    form_url = URL_NEW_ASSIGNMENT_FORM % (course_id)
    create_url = URL_ASSIGNMENTS % (course_id)

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
