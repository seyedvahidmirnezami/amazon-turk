import os
import requests
import mimetypes
import zipfile
import shutil
import time
import io
import json
import logging
from typing import Tuple, BinaryIO, List, Union

try:
    from scipy import misc
except ImportError:
    misc = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

import webbrowser

logger = logging.getLogger(__name__)


def _is_image(path: str) -> bool:
    path = path.lower()
    return path.endswith('.png') or path.endswith('.jpg')


def _clamp_img_size(img, max_width: int = None, max_height: int = None) -> Tuple[float, float]:
    size = img.shape[0:2]
    scale = (max_height / size[0] if max_height else 1.0, max_width / size[1] if max_width else 1.0)
    scale = min(scale[0], scale[1])
    return round(size[0] * scale), round(size[1] * scale)


class QualtricsTagger:
    """
    Class for creating and retrieving results from Qualtrics surveys designed
    around image annotation.
    """
    def __init__(self, api_token: str, url_base: str = 'https://qualtrics.com'):
        """
        :param api_token: API token, found by clicking the top-right portrait -> "Account Settings" -> "Qualtrics IDs"
                          in the Qualtrics web interface.
                          Note that API permissions must also be granted to you by your organization,
                          or you will encounter authorization errors when using the API.
        :param url_base: Base URL for accessing Qualtrics. Usually "https://[organization].qualtrics.com".
                         Must start with "https" and NOT end in a trailing slash.
        """

        self.api_token = api_token
        assert url_base.startswith('https')
        assert not url_base.endswith('/')
        self.url_base = url_base
        self.api_endpoint = url_base + '/API/v3/'

    def create(self, survey_name: str, images_dir: str, library_id: str, templates_dir: str = './templates',
               max_image_width: Union[int, None] = None) -> str:
        """
        Create a new survey.
        :param survey_name: Human-friendly name of the survey. May contain spaces.
        :param images_dir: Directory containing image data. Will be searched recursively.
                           Folder structure will be maintained.
        :param library_id: Library ID to upload to.
        :param templates_dir: Directory to load survey templates from.
                              templates_dir/question.txt: template for each "image tagger" question.
                              templates_dir/survey_header.txt: start of the survey. Include custom questions here.
                              templates_dir/header.html: will prompt user to copy contents into Qualtrics header field.
        :param max_image_width: Optional maximum width of the image. If images exceed this width, they will be
                                downscaled using SciPy's misc module (i.e. pillow). Use None to disable (default).
        :return: the survey ID of the newly-created survey
        """
        # directory for storing temporary files
        work_dir = os.path.join(os.path.dirname(images_dir), os.path.basename(images_dir) + '_qualtrics')

        # build list of image files in images_dir
        images = []
        for dirpath, dirnames, filenames in os.walk(images_dir):
            for filename in filenames:
                path = os.path.join(dirpath, filename)
                if _is_image(path):
                    relpath = os.path.relpath(path, images_dir)
                    if '|' in relpath:
                        raise RuntimeError(relpath + ': image paths cannot contain the pipe symbol (|).')
                    images.append(relpath)
        print(len(images), "images found.")

        # preprocess
        for image in images:
            inpath = os.path.join(images_dir, image)
            outpath = os.path.join(work_dir, image)
            outdir = os.path.dirname(outpath)

            # make sure directory for outfile exists
            if not os.path.exists(outdir):
                os.makedirs(outdir)

            print("Processing", inpath)
            self._process_image(inpath, outpath, max_image_width)

        # upload to Qualtrics
        image_ids = []
        for image in images:
            path = os.path.join(work_dir, image)
            category = os.path.basename(images_dir)

            print("Uploading", path)
            id = self._upload_image(path, category, library_id)
            image_ids.append(id)

        # generate survey.txt
        survey_path = os.path.join(work_dir, 'survey.txt')
        with open(survey_path, 'wt') as outf:
            # write header
            with open(os.path.join(templates_dir, 'survey_header.txt'), 'r') as headerf:
                header_template = headerf.read()

            # add embedded data declarations
            ed_decls = ['[[ED:' + self.image_path_to_ed(p) + ']]' for p in images]
            text = header_template.replace('{ed_declarations}', '\n'.join(ed_decls))
            outf.write(text)
            outf.write('\n')

            # load question template from question.txt
            with open(os.path.join(templates_dir, 'question.txt'), 'r') as templatef:
                question_template = templatef.read()

            # write questions to survey.txt
            for image, image_id in zip(images, image_ids):
                image_url = self._graphic_id_to_url(image_id)
                text = question_template\
                    .replace('{image_path}', image)\
                    .replace('{image_url}', image_url)\
                    .replace('{image_id}', image_id)\
                    .replace('{image_ed}', self.image_path_to_ed(image))
                outf.write(text)
                outf.write('\n')

        # import (create) survey online
        survey_id = self._import_survey(survey_name, survey_path)
        # shutil.rmtree(work_dir)  # delete work dir

        header_html_path = os.path.join(templates_dir, 'header.html')
        if os.path.exists(header_html_path):
            print("The survey has been created, but this survey has a header.html file that must be manually copied "
                  "and pasted into Qualtrics.")
            if pyperclip:
                with open(header_html_path, 'r') as f:
                    pyperclip.copy(f.read())
                print("The header.html file has been copied to your clipboard.")
            else:
                print("Please open '" + header_html_path + "' and copy the contents into your clipboard.")

            url = self.url_base + '/ControlPanel/?ClientAction=EditSurvey&Section=' + survey_id
            webbrowser.open_new_tab(url)
            print("Select the survey, click the 'Look and Feel' button, click the 'Advanced' tab, paste into the "
                  "'Header' textbox and click 'Save'.")
            input("Press enter to continue.")

        return survey_id

    @staticmethod
    def _process_image(in_path: str, out_path: str, max_image_width: Union[int, None]) -> None:
        """
        Processes in_path and saves the result in out_path.
        This implementation down-samples images to a max width of 900px while maintaining aspect ratio.
        """
        # skip if out file was already created (by a previous run)
        if os.path.exists(out_path):
            return

        if not max_image_width:
            # if we're not changing the image, make a (relative) symlink if possible to save space
            # note Windows doesn't support symlinks
            try:
                in_rel = os.path.relpath(in_path, os.path.dirname(os.path.abspath(out_path)))
                os.symlink(in_rel, out_path)
            except:
                shutil.copy(in_path, out_path)
        else:
            if not misc:
                raise ImportError('scipy.misc could not be imported. Image downscaling cannot be performed.')
            img = misc.imread(in_path)
            new_size = _clamp_img_size(img, max_width=max_image_width)
            new_img = misc.imresize(img, new_size, interp="bicubic")
            misc.imsave(out_path, new_img)

    def _upload_image(self, path: str, folder: str, library_id: str) -> str:
        """
        Uploads an image file to a Qualtrics graphics library.
        :param path: path to image file
        :param folder: Qualtrics library folder name to upload to
        :param library_id: Qualtrics library ID to upload into (get from "Qualtrics IDs" page)
        :return: Qualtrics ID corresponding to the uploaded image (IM_*)
        """
        url = self.api_endpoint + 'libraries/' + library_id + '/graphics'

        headers = {
            'X-API-TOKEN': self.api_token
        }

        filename = os.path.basename(path)
        filetype = mimetypes.guess_type(filename)[0]
        files = {
            'file': (filename, open(path, 'rb'), filetype)
        }

        data = {'folder': folder} if (folder and len(folder) > 0) else {}
        r = requests.post(url, headers=headers, data=data, files=files)
        logger.debug(r.text)
        return r.json()['result']['id']

    def _import_survey(self, name: str, path: str) -> str:
        """
        Import a survey text file into Qualtrics. See here for the format:
        https://www.qualtrics.com/support/survey-platform/survey-module/survey-tools/general-tools/import-and-export-surveys/
        :param name: human-friendly name for the survey
        :param path: path to the survey text file
        :return: the newly-created survey's ID (SV_*)
        """
        url = self.api_endpoint + 'surveys'

        headers = {
            'X-API-TOKEN': self.api_token
        }

        filename = os.path.basename(path)
        files = {
            'file': (filename, open(path, 'rb'), 'application/vnd.qualtrics.survey.txt')
        }

        data = {'name': name}
        r = requests.post(url, headers=headers, data=data, files=files)
        logger.debug(r.text)
        return r.json()['result']['id']

    def _graphic_id_to_url(self, graphic_id: str) -> str:
        """
        Converts a Qualtrics graphic resource ID (i.e. from _upload_image) to the URL it can be viewed at.
        :param graphic_id: resource ID (IM_*)
        :return: URL for viewing graphic_id
        """
        return self.url_base + '/WRQualtricsControlPanel/Graphic.php?IM=' + graphic_id

    def image_path_to_ed(self, path: str) -> str:
        """
        :return: Embedded data field name corresponding to the given image.
        """
        return "anno_" + path.replace(os.path.sep, '|')

    def ed_to_image_path(self, ed):
        """
        :return: Converts embedded data name to image path.
        """
        assert ed.startswith('anno_')
        return ed[5:].replace('|', os.path.sep)

    def _generate_report(self, survey_id: str, format_name: str):
        """
        Submits a request for a report to be generated using the Qualtrics API.
        Reports are generated asynchronously - you will need to call _get_export_result to get the actual report.
        :param survey_id: survey ID (SV_*)
        :param format_name: format for report (csv, json, tsv, xml, ...)
        :return: export ID
        """

        url = self.api_endpoint + 'responseexports'
        headers = {
            'X-API-TOKEN': self.api_token,
        }
        data = {
            'surveyId': survey_id,
            'format': format_name
        }
        r = requests.post(url, headers=headers, json=data)
        logger.debug(r.text)
        return r.json()['result']['id']

    def _get_export_progress(self, export_id: str) -> dict:
        """
        Returns the current status of the report.
        :param export_id: report ID (returned by _generate_report)
        :return: dict containing current status data; contents vary depending on report status.
                 See: https://api.qualtrics.com/docs/get-response-export-progress
                 (this call returns the 'result' portion of the response)
        """
        url = self.api_endpoint + 'responseexports/' + export_id
        headers = {
            'X-API-TOKEN': self.api_token,
        }
        r = requests.get(url, headers=headers)
        logger.debug(r.text)
        return r.json()['result']

    def _get_export_result(self, export_id: str, out_file: Union[BinaryIO, io.FileIO]) -> None:
        """
        Waits until a report is ready, then downloads it into out_file.
        Format is a ZIP file containing potentially multiple files in the requested format,
        as per the Qualtrics API.
        :param export_id: export ID (from _generate_report)
        :param out_file: file-like object to save downloaded data in
        """
        while True:
            r = self._get_export_progress(export_id)
            if r['status'] == 'in progress':
                print('Waiting for report (' + str(r['percentComplete']) + '% complete)...')
                time.sleep(1.5)
            else:
                if r['status'] == 'complete':
                    print("Report exported. Downloading from: " + r['file'])
                    r = requests.get(r['file'], headers={'X-API-TOKEN': self.api_token}, stream=True)
                    shutil.copyfileobj(r.raw, out_file)
                    break
                else:
                    raise RuntimeError('Report could not be exported: ' + r['info']['reason'])

    def download_results(self, survey_id: str, out_file: Union[BinaryIO, io.FileIO], format_name: str = 'json') -> None:
        """
        Exports data from Qualtrics into a ZIP file and saves it in out_file.
        See "get_responses" if you don't want to deal with the ZIP file.
        :param format_name:
        :param survey_id: survey ID
        :param out_file: where to put the data (it will be in a ZIP archive)
        :param format_name: format for report (csv, json, tsv, xml, ...)
        """
        export_id = self._generate_report(survey_id, format_name)
        self._get_export_result(export_id, out_file)

    def get_responses(self, survey_id: str) -> List[dict]:
        """
        Exports data from Qualtrics, parses the zip file, and returns all responses as a list of dicts.
        Note that this data can contain unfinished surveys.
        Note that this function is all in-memory and may fail for an extremely large amount of response data.
        :param survey_id: survey ID
        :return: list of responses
        """
        # download repsonse data (zip file) into an in-memory buffer
        zipf = io.BytesIO()
        self.download_results(survey_id, zipf)

        # loop through response data in zip files
        # (may be in multiple files - see https://api.qualtrics.com/docs/response-exports)
        responses = []
        with zipfile.ZipFile(zipf, mode='r') as zip:
            for filename in zip.namelist():
                data = json.loads(zip.read(filename))
                responses += data['responses']
        return responses
