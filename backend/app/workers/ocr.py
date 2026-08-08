import json
import time
import tempfile
import os
from app.kafka_utils import get_kafka_consumer, produce_event, get_kafka_producer
from app.s3_utils import download_file_bytes
from app.models import SessionLocal, CertificateAnalysis
from app.ocr import run_ocr, extract_fields

consumer = get_kafka_consumer("ocr-worker-group", ["certificate_uploaded"])
producer = get_kafka_producer()

def process_ocr(event):
    analysis_id = event["analysis_id"]
    file_key = event["file_key"]
    content_type = event["content_type"]
    
    print(f"[OCR] Processing {analysis_id}")
    
    try:
        # Download from Minio
        file_bytes = download_file_bytes(file_key)
        
        # Save to temp file for processing
        ext = ".pdf" if content_type == "application/pdf" else ".jpg"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
            
        # Run OCR
        ocr_text = run_ocr(tmp_path, content_type)
        extracted = extract_fields(ocr_text)
        
        # Update DB
        db = SessionLocal()
        analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).first()
        if analysis:
            analysis.ocr_text = ocr_text[:4000]
            analysis.extracted_fields = extracted
            db.commit()
        db.close()
        
        # Publish completion event
        produce_event(producer, "ocr_completed", analysis_id, {"analysis_id": analysis_id, "file_key": file_key, "content_type": content_type, "ocr_text": ocr_text})
        print(f"[OCR] Completed {analysis_id}")
        
    except Exception as e:
        print(f"[OCR] Failed {analysis_id}: {e}")
        db = SessionLocal()
        analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).first()
        if analysis:
            analysis.status = "failed"
            db.commit()
        db.close()
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)

if __name__ == "__main__":
    print("[OCR] Worker started. Waiting for events...")
    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            print(f"[OCR] Consumer error: {msg.error()}")
            continue
        
        event = json.loads(msg.value().decode('utf-8'))
        process_ocr(event)
