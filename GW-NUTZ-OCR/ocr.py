import os
import sqlite3
import logging
import werkzeug
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
import boto3
from PIL import Image
import cv2
import numpy as np
from paddleocr import PaddleOCR, draw_ocr
from io import BytesIO
import requests

import zipfile

app = Flask(__name__, template_folder='templates', static_folder='static', static_url_path='/')

os.environ['AWS_ACCESS_KEY_ID'] = 'AKIA2UC3B7Z5ZIYJZ2YA'
os.environ['AWS_SECRET_ACCESS_KEY'] = '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'
os.environ['AWS_REGION'] = 'ap-southeast-1'
# Load AWS credentials from environment variables
aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
region_name = os.getenv('AWS_REGION')
aws_bucket = 'sw-ocr'

def already_exist_data():
    conn = sqlite3.connect('my_database.db')
    c = conn.cursor()
    already_exist = []
    db_path = "my_database.db"  # Replace with your database file path
    table_name_to_check = "images_file_database"
    try:
        conn = sqlite3.connect(db_path)
        if table_exists(conn, table_name_to_check):
            print(f"The table '{table_name_to_check}' exists.")
            exist_data = c.execute("SELECT image_name FROM images_file_database").fetchall()
            for data in exist_data:
                already_exist.append(data[0])
        else:
            print(f"The table '{table_name_to_check}' does not exist.")

    except sqlite3.Error as e:
        print(f"Error: {e}")
    finally:
        conn.close()

    return already_exist


def table_exists(conn, table_name):
    """Check if a table exists in the SQLite database."""
    query = f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';"
    cursor = conn.execute(query)
    return cursor.fetchone() is not None



# Configure S3 credentials
s3 = boto3.client(
    's3',
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key,
    region_name=region_name
)




def remove_consecutive_duplicates(nums):
    result = []
    prev_num = None
    for num in nums:
        if num != prev_num:
            result.append(num)
        prev_num = num
    return result


# Function to search for images based on input text
def approximate_match(input_text, data_list):
    approximate_match_list = []
    for data, data2 in data_list:
        if input_text in data2 and input_text != data2:
            approximate_match_list.append(data)
    return remove_consecutive_duplicates(approximate_match_list)


# Define the functions
def crop_and_zoom(img, x, y, zoom_factor):
    img1 = img.resize((8000, 8000), Image.LANCZOS)
    width, height = img1.size
    zoom_ratio = zoom_factor * 2
    cropped_img = img1.crop(
        (x - width / zoom_ratio, y - height / zoom_ratio, x + width / zoom_ratio, y + height / zoom_ratio))
    return cropped_img.resize((width, height), Image.LANCZOS)


def preprocess_image(image):
    resized_image = cv2.resize(image, (8000, 8000))  # Adjust size as needed
    return resized_image


def integrate_functions(img_pil, x, y, zoom_factor):
    cropped_zoomed_img_pil = crop_and_zoom(img_pil, x, y, zoom_factor)
    cropped_zoomed_img_cv = cv2.cvtColor(np.array(cropped_zoomed_img_pil), cv2.COLOR_RGB2BGR)
    preprocessed_img = preprocess_image(cropped_zoomed_img_cv)
    return preprocessed_img


def fetch_image_from_s3(bucket_name, object_key):
    s3 = boto3.client('s3')
    response = s3.get_object(Bucket=bucket_name, Key=object_key)
    image_data = response['Body'].read()
    img_pil = Image.open(BytesIO(image_data))
    return img_pil


def list_images_from_s3(bucket_name):
    s3 = boto3.client('s3')
    objects = s3.list_objects_v2(Bucket=bucket_name)
    return [obj['Key'] for obj in objects.get('Contents', [])]


def ocr_with_preprocessing_from_s3(bucket_name, x, y, zoom_factor):
    image_dict = {}
    img_name = []
    ocr = PaddleOCR(use_angle_cls=True, lang='en')  # Initialize PaddleOCR
    image_keys = list_images_from_s3(bucket_name)  # Get all image keys from the bucket
    already_exist_data_value = already_exist_data()
    for object_key in image_keys:
        if object_key not in already_exist_data_value:
            img_pil = fetch_image_from_s3(bucket_name, object_key)  # Fetch image from S3
            preprocessed_img = integrate_functions(img_pil, x, y, zoom_factor)  # Apply preprocessing

            # Perform OCR on the preprocessed image
            result = ocr.ocr(preprocessed_img)
            print(f"OCR result for {object_key}: {result}")
            if None != result[0]:
                for data in result:
                    for i in data:
                        if i[1][0].isalpha():
                            img_name.append(i[1][0].lower())
                        else:
                            img_name.append(i[1][0])

                image_dict[object_key] = img_name
                print("image:", image_dict)
                img_name = []
    return image_dict


# Example usage
def initialize_database_with_text(image_text_dict):
    conn = sqlite3.connect('my_database.db')
    c = conn.cursor()

    # Create tables if not exist
    c.execute(
        "CREATE TABLE IF NOT EXISTS images_file_database (id INTEGER PRIMARY KEY AUTOINCREMENT, image_name TEXT);")
    c.execute(
        "CREATE TABLE IF NOT EXISTS images_database (id INTEGER PRIMARY KEY, image_id TEXT NOT NULL, image_text TEXT);")

    # Insert image names into images table
    for image_name in image_text_dict.keys():
        c.execute("INSERT INTO images_file_database (image_name) VALUES (?)", (image_name,))

    # Insert image text data into image_data table
    for image_name, image_text_list in image_text_dict.items():
        for image_text in image_text_list:
            c.execute("INSERT INTO images_database (image_id, image_text) VALUES (?, ?)", (image_name, image_text))
    conn.commit()
    conn.close()


def train_model():
    bucket_name = aws_bucket
    x, y, zoom_factor = 4000, 5000, 1  # Example values for cropping and zooming
    # Perform OCR with preprocessing for all images in the S3 bucket
    image = ocr_with_preprocessing_from_s3(bucket_name, x, y, zoom_factor)
    initialize_database_with_text(image)


def create_presigned_url(bucket_name, object_key, expiration=3600):
    s3 = boto3.client('s3')
    try:
        response = s3.generate_presigned_url('get_object',
                                             Params={'Bucket': bucket_name,
                                                     'Key': object_key},
                                             ExpiresIn=expiration)
    except Exception as e:
        logging.error(f"Error generating presigned URL: {e}")
        return None
    return response


def search_images_by_text(input_text):
    image_list = []
    conn = sqlite3.connect("my_database.db")
    cursor = conn.cursor()
    approximate_match_list = cursor.execute(
        "SELECT image_id, image_text FROM images_database").fetchall()
    list_data = cursor.execute(
        "SELECT images_database.image_id FROM images_database WHERE images_database.image_text = '{}'".format(
            input_text))
    approximate_match_data = approximate_match(input_text, approximate_match_list)
    for data in list_data.fetchall():
        image_list.append(data[0])

    conn.close()
    return image_list + list(approximate_match_data)


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/upload')
def upload():
    return render_template('upload.html')


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'files' not in request.files:
        return "No file part"

    files = request.files.getlist('files')

    for file in files:
        if file.filename == '':
            continue

        filename = secure_filename(file.filename)
        s3.upload_fileobj(
            file,
            aws_bucket,
            filename,
            ExtraArgs={
                "ContentType": file.content_type
            }
        )

    return render_template('upload.html')


@app.route('/train', methods=['POST'])
def train():
    train_model()
    return jsonify({'status': 'completed'})


@app.route('/search', methods=['POST'])
def search():
    global image_urls
    input_text = request.form.get('bib_number')
    if input_text:
        if input_text.isalpha():
            input_text = input_text.lower()
        image_ids = search_images_by_text(input_text)
        image_paths = [create_presigned_url(aws_bucket, key) for key in image_ids if create_presigned_url(aws_bucket, key)]
        message = "" if image_ids else "No image found"
    else:
        image_paths = []
        message = "Please enter a BIB number"
    return render_template('index.html', images=image_paths, message=message)


@app.route('/download', methods=['POST'])
def download_images():
    image_urls = request.json['urls']

    if len(image_urls) == 1:
        response = requests.get(image_urls[0])
        image = BytesIO(response.content)
        image.seek(0)
        return send_file(image, as_attachment=True, download_name='image.png')
    else:
        memory_file = BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for idx, url in enumerate(image_urls):
                response = requests.get(url)
                image = BytesIO(response.content)
                zf.writestr(f'image_{idx + 1}.png', image.getvalue())
        memory_file.seek(0)
        return send_file(memory_file, as_attachment=True, download_name='images.zip')


if __name__ == '__main__':
   app.run(debug=True)
