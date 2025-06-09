from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import openai
import os

# Устанавливаем API-ключ
openai.api_key = os.getenv("OPENAI_API_KEY")

app = FastAPI()

# Разрешаем CORS — чтобы можно было отправлять запросы с сайта
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Можно заменить на ["https://biostop.by"] для безопасности
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Временный маршрут: генерируем картинку с нужным цветом крыши
@app.post("/api/recolor")
async def recolor_roof(image: UploadFile = File(...), color: str = Form(...)):
    prompt = f"A modern house with a roof painted in color {color}, viewed in daylight, beautiful photo, blue sky, lush green grass"

    try:
        response = openai.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        image_url = response.data[0].url
        return JSONResponse(content={"image_url": image_url})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
