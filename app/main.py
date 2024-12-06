import os
import sys
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

# Add the parent directory to the sys path so that modules can be found
base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_path)

from PySubtitle.Options import Options
from PySubtitle.SubtitleProject import SubtitleProject
from PySubtitle.SubtitleTranslator import SubtitleTranslator
from scripts.subtrans_common import CreateOptions, CreateProject, CreateTranslator

app = FastAPI(title="LLM Subtrans API")


@app.post("/translate")
async def translate_subtitles(
    file: UploadFile = File(...),
    server: Optional[str] = Form(None),
    endpoint: Optional[str] = Form(None),
    apikey: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    chat: bool = Form(False),
    systemmessages: bool = Form(False),
    target_language: Optional[str] = Form(None),
    debug: bool = Form(False),
):
    try:
        # Save uploaded file to temporary location
        with tempfile.NamedTemporaryFile(delete=False, suffix=".srt") as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_path = temp_file.name

        # Create options object
        class Args:
            def __init__(self):
                self.input = temp_path
                self.server = server
                self.endpoint = endpoint
                self.apikey = apikey
                self.model = model
                self.chat = chat
                self.systemmessages = systemmessages
                self.target_language = target_language
                self.debug = debug
                # Add other default args as needed
                self.project = None
                self.write_project = False

        args = Args()

        options: Options = CreateOptions(
            args,
            "Local Server",
            api_key=apikey,
            endpoint=endpoint,
            model=model,
            server_address=server,
            supports_conversation=chat,
            supports_system_messages=systemmessages,
        )

        # Create translator and project
        translator: SubtitleTranslator = CreateTranslator(options)
        project: SubtitleProject = CreateProject(options, args)

        # Perform translation
        result = project.TranslateSubtitles(translator)

        # Clean up temp file
        os.unlink(temp_path)

        return JSONResponse(
            content={"message": "Translation completed", "result": result}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
