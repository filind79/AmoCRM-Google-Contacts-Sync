from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import openai
import os
import traceback  # Добавляем для печати ошибок

openai.api_key = os.getenv("OPENAI_API_KEY")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://biostop.by",
        "https://www.biostop.by"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/recolor")
async def recolor_roof(color: str = Form(...)):
    prompt = (
        f"A modern detached house with a sloped roof painted in {color}, "
        f"sunny weather, blue sky, green grass in front, photorealistic image"
    )
    print(f"🎨 Prompt: {prompt}")  # логируем промпт

    try:
        response = openai.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        print("✅ OpenAI response:", response)
        image_url = response.data[0].url
        return JSONResponse(content={"image_url": image_url})
    except Exception as e:
        print("❌ Ошибка при генерации изображения:")
        print(traceback.format_exc())  # покажет причину
        return JSONResponse(status_code=500, content={"error": str(e)})
