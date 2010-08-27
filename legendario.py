import cgi
import logging
import os
import random
import string

from google.appengine.api import images
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app

# Database containing all labels to be appended to the user's photo. The label
# id must be called image_id so that DbFunctions can work on both Labels and
# ImageDB.
class LabelsDb (db.Model):
  image_type = db.StringProperty()
  image_id = db.StringProperty()
  content = db.BlobProperty()

# Database containing all images uploaded by users. The date field is used by
# cronjobs to clean up the table. (TBD)
class ImageDb(db.Model):
  image_type = db.StringProperty()
  image_id= db.StringProperty()
  content = db.BlobProperty()
  date = db.DateTimeProperty(auto_now_add=True)


# Helper functions for accessing DB data.
class DbFunctions:
  def getImage(self, database, image_id):
    imageDb = db.GqlQuery('SELECT * FROM %s WHERE image_id = :1' % database,
                          image_id)
    # We return the first image available with the given image_id
    for image in imageDb:
      return image
    return None

  def getImages(self, database):
    imageDb = db.GqlQuery('SELECT * FROM %s ORDER BY image_id ASC' % database)
    return imageDb

# Serves photos given ids and the database to read from
class GetPhoto(webapp.RequestHandler):
  def get(self):
    image_id = self.request.get('image_id')
    image_db = self.request.get('image_db')
    if image_db == None:
      imabe_db = 'ImageDb'

    if image_db == 'LabelsDb' or image_db == 'ImageDb':
      image = DbFunctions().getImage(image_db, image_id)
      if not image == None: 
        image_type = 'image/png'
        if not image.image_type == None:
          image_type = image.image_type
        self.response.headers['Content-Type'] = image_type
        self.response.out.write(image.content)
        return;

    self.response.headers['Content-Type'] = 'text/html' 
    self.response.set_status(404, 'Not Found') 
    self.response.out.write('Imagem nao encontrada')

# Main page rendering function. You can always do the following to render the
# main page:
#  self.response.out.write((RenderMainPage(error_message='Could not do this')))
#  or
#  self.response.out.write((RenderMainPage(success_content=my_success_content)))
def RenderMainPage(success_content = '', error_message = ''):
    labels = DbFunctions().getImages('LabelsDb')
    template_values = { 
        'labels': [],
        'error_message': error_message,
        'success_content': success_content
    }
    i = 0
    for label in labels:
      label_img = images.Image(label.content)
      template_values['labels'].append({ 'name': 'label%d' % i,
                                         'id': label.image_id })
      i = i + 1
    path = os.path.join(os.path.dirname(__file__), 'index.template')
    return template.render(path, template_values)


# Simple URL handler for the main page.
class MainPage(webapp.RequestHandler):
  def get(self):
    self.response.out.write(RenderMainPage())


# Given the content type return the corresponding appengine type.
def getImageTypeFromContentType(content_type):
  if content_type == 'image/gif':
   # appengine supports gif type as jpeg
   return images.JPEG;
  if content_type == 'image/jpeg':
   return images.JPEG
  if content_type == 'image/png':
   return images.PNG
  return None

# Handler that adds a label to the user photo.
class Legendario(webapp.RequestHandler):
  def post(self):
    self.response.headers['Content-Type'] = 'text/html'
    uploaded_image = self.request.POST['source_image']
    if uploaded_image == None or uploaded_image == '':
      self.response.out.write(RenderMainPage(error_message='Selecione uma foto.'))
      return;

    # extracts the photo type from the uploaded image
    content_type = uploaded_image.type
    image_type = getImageTypeFromContentType(content_type)
    if image_type == None:
      self.response.out.write(
        RenderMainPage(error_message='Tipo de imagem desconhecido. Use imagens JPEG, PNG ou GIF'))
      return;

    image_content = self.request.get('source_image')
    if len(image_content) > (1 << 20): # 1M
      self.response.out.write(RenderMainPage(
            error_message='Sua foto deve ter menos de 1 MB.'))
      return;
 
    label_name = self.request.get('label_name')
    if label_name == None or label_name == '':
      self.response.out.write(RenderMainPage(error_message='Escolha um dos labels.'))
      return;
  
    label = DbFunctions().getImage('LabelsDb', label_name)
    if label == None:
      self.response.out.write(RenderMainPage(
        error_message='Label \'%s\' nao encontrado' % label_name))
      return;

    imageDb = ImageDb()
    image = images.Image(image_content)
    label_img = images.Image(label.content)

    # There is this limitation on the appengine images library that doesn't
    # allow tranformations whose height or width is > 4000, so lets reduce image
    # right away. The label width and height is always guaranteed to be < than
    # 1000 pixels so, if we need to resize something, this thing is the user
    # height. The width will never exceed this limitation because we always
    # scale down the bigger photo and label width is always < than 1000. 
    if image.height + label_img.height > 4000:
      # Since we know that label height size is not the reason for the 4000
      # exceed, lets resize image down. 
      image.resize(height=(4000 - label_img.height))
      image = images.Image(image.execute_transforms(image_type))

    # Make image and label to have the same width. Scale down the bigger one.
    if label_img.width > image.width:
      label_img.resize(width=image.width)
      label_img = images.Image(label_img.execute_transforms(
          getImageTypeFromContentType(label.image_type)))
    else:
      image.resize(width=label_img.width)
      image = images.Image(image.execute_transforms(image_type))

    # now images have the same width. Height will never exceed the 4000 limit.
    result = images.composite([(image, 0, 0, 1.0, images.TOP_RIGHT),
                               (label_img, 0, image.height, 1.0,
                                images.TOP_RIGHT) ], image.width,
                               image.height + label_img.height,
                               images.JPEG)

    # Due to some weird behaviour of the transformation library, it may be the
    # case that the result is bigger than the len(label_img) + len(image). Why,
    # why, why??
    if len(result) > (1 << 20):
      self.response.out.write(RenderMainPage(error_message='''Sua imagem ficou
      muito grande depois de acrescentar a legenda. Reduza o tamanho da sua
      imagem original. Se isso nao resolver, tente reduzir suas dimensoes ou
      sua resolucao. Se nada funcionar, mande um email para ademirao@gmail.com'''))
      return;

    key_range = range(random.randint(24,32))
    key_chars = string.ascii_letters;
    imageDb.image_id = (''.join(random.choice(key_chars) for x in key_range))
    imageDb.image_type = "image/jpeg"
    imageDb.content = db.Blob(result) 
    imageDb.put();
    self.response.out.write(RenderMainPage({ 'image_id': imageDb.image_id }))

class AddLabel(webapp.RequestHandler):
  def post(self):
    labelDb = LabelsDb()
    uploaded_label = self.request.POST['source_label']
    content_type = uploaded_label.type
    label_type = getImageTypeFromContentType(content_type)
    if label_type == None:
      self.response.out.write('Tipo de imagem desconhecido. Use imagens JPEG, PNG ou GIF')
      return;

    logging.info(content_type)
    label_img = images.Image(self.request.get('source_label'))
    label_data = self.request.get('source_label')
    # Make sure label width is < than 1000
    if label_img.width > 1000:
      label_img.resize(width=1000)
      label_data = label_img.execute_transforms(label_type)

    if label_img.height > 500 :
      label_img.resize(height=500)
      label_data = label_img.execute_transforms(label_type)

    labelDb.image_id = self.request.POST['label_name']
    labelDb.content = db.Blob(label_data)
    labelDb.image_type = content_type
    labelDb.put();
    self.response.out.write('Label added! Name %s' % labelDb.image_id)

application = webapp.WSGIApplication(
                                     [('/', MainPage),
                                      ('/add_label', AddLabel),
                                      ('/dilmefiqueme', Legendario),
                                      ('/photo', GetPhoto)], debug=True)

def main():
  run_wsgi_app(application)

if __name__ == '__main__':
  main()

