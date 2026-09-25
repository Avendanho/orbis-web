import os
import json
from typing import Callable, Tuple

# Limites para não estourar as restrições dos provedores de imagem.
MAX_IMAGES = 6
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB por imagem


def _cap_images(image_paths):
    """Filtra e limita as imagens: no máximo MAX_IMAGES, cada uma < MAX_IMAGE_BYTES.
    Retorna sempre uma lista (possivelmente vazia)."""
    if not image_paths:
        return []
    kept = []
    for p in image_paths:
        try:
            if os.path.getsize(p) < MAX_IMAGE_BYTES:
                kept.append(p)
        except OSError:
            continue
        if len(kept) >= MAX_IMAGES:
            break
    return kept

def get_llm_client() -> Tuple[Callable[[str, str], str], str]:
    """
    Retorna uma tupla contendo:
    1. Uma função analyze_article(system_prompt: str, user_prompt: str) -> str
       que executa a chamada ao LLM e retorna a string (esperada como JSON)
    2. Uma string com o nome do provider utilizado (ex: "Gemini 2.5 Flash")
    
    A hierarquia é: Gemini -> Claude -> OpenAI -> Ollama
    """
    
    # 1. Tentar Gemini
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            from google import genai
            from google.genai import types
            # Testa o cliente rapidamente
            client = genai.Client(api_key=gemini_key)
            
            def gemini_analyze(system_prompt: str, user_prompt: str, image_paths: list = None) -> str:
                contents = []
                for img_path in _cap_images(image_paths):
                    try:
                        from PIL import Image
                        contents.append(Image.open(img_path))
                    except Exception:
                        pass
                contents.append(user_prompt)

                # O Gemini prefere receber system instructions na configuração do modelo.
                # `gemini-2.5-flash` é um id de flash atualmente válido (mesmo usado no backend).
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        temperature=0.0
                    )
                )
                return response.text

            return gemini_analyze, "Gemini 2.5 Flash"
        except Exception as e:
            print(f"⚠️ Aviso: Falha ao inicializar Gemini ({e}). Tentando próximo provider...")

    # 2. Tentar Claude (Anthropic)
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            
            def claude_analyze(system_prompt: str, user_prompt: str, image_paths: list = None) -> str:
                content_list = []
                import base64
                import mimetypes
                for img_path in _cap_images(image_paths):
                    try:
                        mime_type, _ = mimetypes.guess_type(img_path)
                        if not mime_type: mime_type = "image/jpeg"
                        with open(img_path, "rb") as image_file:
                            encoded = base64.b64encode(image_file.read()).decode("utf-8")
                        content_list.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": encoded
                            }
                        })
                    except Exception:
                        pass

                content_list.append({"type": "text", "text": user_prompt})

                # Modelos atuais do Claude rejeitam o prefill de assistant ("{") com 400,
                # e claude-sonnet-5 também rejeita parâmetros de sampling (temperature).
                # O formato JSON é garantido via instrução no system/user prompt.
                response = client.messages.create(
                    model="claude-sonnet-5",
                    max_tokens=8192,  # margem para protocolos longos → evita truncamento do JSON
                    system=system_prompt + "\n\nResponda EXCLUSIVAMENTE com um único objeto JSON válido, sem texto antes ou depois e sem cercas de código.",
                    messages=[
                        {"role": "user", "content": content_list}
                    ]
                )

                return response.content[0].text

            return claude_analyze, "Claude Sonnet 5"
        except Exception as e:
            print(f"⚠️ Aviso: Falha ao inicializar Claude ({e}). Tentando próximo provider...")

    # 3. Tentar OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            
            def openai_analyze(system_prompt: str, user_prompt: str, image_paths: list = None) -> str:
                content_list = []
                capped = _cap_images(image_paths)
                if capped:
                    import base64
                    for img_path in capped:
                        try:
                            with open(img_path, "rb") as image_file:
                                encoded = base64.b64encode(image_file.read()).decode("utf-8")
                            content_list.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}
                            })
                        except Exception:
                            pass
                content_list.append({"type": "text", "text": user_prompt})
                
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": content_list}
                    ],
                    response_format={"type": "json_object"},
            temperature=0.0
                )
                return response.choices[0].message.content
                
            return openai_analyze, "OpenAI gpt-4o-mini"
        except Exception as e:
            print(f"⚠️ Aviso: Falha ao inicializar OpenAI ({e}). Tentando próximo provider...")

    # 4. Fallback Ollama Local
    import openai
    model_name = os.environ.get("LLM_MODEL", "qwen2.5")
    client = None
    
    # Tenta localhost primeiro
    try:
        client = openai.OpenAI(base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"), api_key="ollama")
        client.with_options(timeout=5.0, max_retries=0).models.list()  # sonda rápida
    except Exception:
        # Tenta docker network fallback se localhost falhar
        try:
            client = openai.OpenAI(base_url="http://host.docker.internal:11434/v1", api_key="ollama")
            client.with_options(timeout=5.0, max_retries=0).models.list()  # sonda rápida
        except Exception:
            # Sem isso `client` ficava apontando para um servidor inacessível e a
            # mensagem clara de "nenhum provider" abaixo nunca era exibida.
            client = None
            
    def ollama_analyze(system_prompt: str, user_prompt: str, image_paths: list = None) -> str:
        if not client:
            raise Exception("Nenhum provider de IA disponível. Configure as API Keys no .env ou inicie o Ollama local.")
            
        content_list = []
        # Modelos Ollama locais costumam ser somente-texto (ex.: qwen2.5). Enviar
        # imagens quebra a chamada. Só inclui imagens se OLLAMA_VISION=1.
        capped = _cap_images(image_paths) if os.environ.get("OLLAMA_VISION") == "1" else []
        if capped:
            import base64
            for img_path in capped:
                try:
                    with open(img_path, "rb") as image_file:
                        encoded = base64.b64encode(image_file.read()).decode("utf-8")
                    content_list.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}
                    })
                except Exception:
                    pass
        content_list.append({"type": "text", "text": user_prompt})

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_list}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            extra_body={"options": {"num_ctx": 32768, "num_predict": 2048}}
        )
        return response.choices[0].message.content
        
    return ollama_analyze, f"Ollama Local ({model_name})"
