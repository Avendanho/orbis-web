#!/usr/bin/env python3
import os
import sys

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

def summarize_pdf(pdf_path: str):
    if not pdfplumber:
        print("pdfplumber not installed. Please install it to use ai_summarizer.")
        return
        
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("No AI API Key found in environment variables (GEMINI_API_KEY / OPENAI_API_KEY).")
        return
        
    try:
        text = ""
        with pdfplumber.open(pdf_path) as pdf:
            # Extract first 2 pages for abstract/introduction and last 2 pages for conclusion
            pages_to_extract = []
            total_pages = len(pdf.pages)
            if total_pages > 4:
                pages_to_extract = [0, 1, total_pages - 2, total_pages - 1]
            else:
                pages_to_extract = list(range(total_pages))
                
            for p in pages_to_extract:
                page_text = pdf.pages[p].extract_text()
                if page_text:
                    text += page_text + "\n"
                    
        if not text.strip():
            print(f"Could not extract text from {pdf_path}")
            return
            
        print(f"Text extracted for {pdf_path}. LLM processing requires standard API calls implementation depending on the provider.")
        
        # Here you would typically call the LLM API using the `text` variable
        # with a prompt like: "Summarize this academic paper: abstract, key findings, and conclusion."
        
    except Exception as e:
        print(f"Error summarising {pdf_path}: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        summarize_pdf(sys.argv[1])

