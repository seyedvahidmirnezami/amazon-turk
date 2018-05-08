from qualtrics_tagger import QualtricsTagger
import logging
logging.basicConfig(level=logging.DEBUG)

API_TOKEN = 'CvplZAo1oNvpzQ21FOK4hMhgiPPCeYLdALJso4mY'
LIBRARY_ID = 'UR_bPkvWXcu43ayAbX'

tagger = QualtricsTagger(api_token=API_TOKEN, url_base='https://iastate.qualtrics.com')

# create a survey out of the images in test_images
# example survey templates include 'freeform' (draw a free-form polygon)
# and 'single_line' (draw 1 line on the image)
survey_id = tagger.create('MTurk_Demo_Vahid_single_line', './test_images', LIBRARY_ID, templates_dir='./templates/freeform', max_image_width=999999)
print(survey_id)

# # ...later...
# # download responses as a zip file (containing json, csv, tsv, or xml files)
# with open('data.zip', 'wb') as f:
#     tagger.download_results(survey_id, f, format_name='json')
#
# # or, parse responses in Python
# responses = tagger.get_responses(survey_id)
# print(responses)
