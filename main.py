from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
import base64
import os
from dotenv import load_dotenv   # ← добавьте эту строку
load_dotenv()                    # ← и эту

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI()
# ===== Chat endpoint (SSE) =====
from fastapi import Request
from sse_starlette.sse import EventSourceResponse

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = (
    "Ты — эксперт по бережной мойке и покраске шиферных крыш в Беларуси. "
    "Отвечай дружелюбно, до 200 слов, в конце предлагай услуги biostop.by. "
    "Вопросы вне тематики крыш вежливо отклоняй."
)

@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    user_messages = body.get("messages", [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + user_messages

    async def token_stream():
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            stream=True,
            temperature=0.3,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield {"data": delta}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(token_stream(), media_type="text/event-stream")
# ===== /Chat endpoint =====


app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://biostop.by", "https://www.biostop.by"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def encode_image(uploaded_file: UploadFile) -> str:
    image_data = uploaded_file.file.read()
    return base64.b64encode(image_data).decode('utf-8')

@app.post("/process")
async def process_roof(
    mode: str = Form(...),
    color: str = Form(None),
    image: UploadFile = File(...)
):
    try:
        base64_image = encode_image(image)

        if mode == "wash":
            prompt = (
                "Сделай так, чтобы на этом фото грязная замшелая крыша из волнового шиферных листов "
                "выглядела как после высококачественной мойки в светло-серых тонах. "
                "Небо голубое, для более яркого контраста."
            )
        elif mode == "paint" and color:
            prompt = (
                f"Сделай так, чтобы на этом фото грязная замшелая крыша из волнового шиферных листов "
                f"выглядела как после высококачественной покраски в цвет {color}. "
                "Небо голубое, для более яркого контраста."
            )
        else:
            return JSONResponse(status_code=400, content={"error": "Неверные параметры."})

        # Отправка запроса с изображением и prompt
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            n=1,
            image=base64_image,
        )

        image_url = response.data[0].url
        return JSONResponse(content={"url": image_url})

    except Exception as e:
        print("❌ Ошибка при генерации:", str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})
