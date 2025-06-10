from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://biostop.by", "https://www.biostop.by"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/recolor")
async def recolor_roof(color: str = Form(...)):
    prompt = (
        f"A photorealistic image of a modern detached house with a sloped roof, "
        f"roof color: {color}, sunny weather, blue sky, green lawn in front"
    )
    print("🎨 Prompt:", prompt)

    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            n=1
        )
        image_url = response.data[0].url
        return JSONResponse(content={"image_url": image_url})
    except Exception as e:
        print("❌ Ошибка при генерации изображения:", e)
        return JSONResponse(status_code=500, content={"error": str(e)})
