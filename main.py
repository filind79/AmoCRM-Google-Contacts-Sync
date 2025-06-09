from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse
import openai
import shutil
import os
import uuid

openai.api_key = os.getenv("OPENAI_API_KEY")

app = FastAPI()

@app.post("/api/recolor")
async def recolor_roof(image: UploadFile = File(...), color: str = Form(...)):
    temp_filename = f"temp_{uuid.uuid4().hex}.png"
    with open(temp_filename, "wb") as buffer:
        shutil.copyfileobj(image.file, buffer)

    try:
        response = openai.images.edit(
            image=open(temp_filename, "rb"),
            mask=open(temp_filename, "rb"),  # пока используем то же изображение
            prompt=f"Paint the roof in {color} color",
            n=1,
            size="1024x1024"
        )
        image_url = response["data"][0]["url"]
        os.remove(temp_filename)
        return JSONResponse(content={"image_url": image_url})
    except Exception as e:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)
        return JSONResponse(status_code=500, content={"error": str(e)})
