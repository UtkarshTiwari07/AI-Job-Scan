"""
PDF Parser Utility - Extracts text from PDF resumes
"""
from PyPDF2 import PdfReader
import io


def extract_text_from_pdf(pdf_file) -> str:
    """
    Extract text content from a PDF file.
    
    Args:
        pdf_file: File-like object or path to PDF file
        
    Returns:
        Extracted text as string
    """
    try:
        if isinstance(pdf_file, str):
            reader = PdfReader(pdf_file)
        else:
            reader = PdfReader(io.BytesIO(pdf_file.read()))
        
        text_content = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_content.append(text)
        
        return "\n".join(text_content)
    except Exception as e:
        raise ValueError(f"Failed to extract text from PDF: {str(e)}")


def extract_text_from_file(file, filename: str) -> str:
    """
    Extract text from uploaded file based on extension.
    
    Args:
        file: File-like object
        filename: Original filename with extension
        
    Returns:
        Extracted text as string
    """
    extension = filename.lower().split('.')[-1]
    
    if extension == 'pdf':
        return extract_text_from_pdf(file)
    elif extension in ['txt', 'text']:
        return file.read().decode('utf-8')
    elif extension == 'docx':
        try:
            from docx import Document
            doc = Document(io.BytesIO(file.read()))
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
            raise ValueError(f"Failed to extract text from DOCX: {str(e)}")
    else:
        # Try to read as plain text
        try:
            return file.read().decode('utf-8')
        except:
            raise ValueError(f"Unsupported file format: {extension}")
