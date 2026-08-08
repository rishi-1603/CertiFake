import json
import os
import tempfile
from app.kafka_utils import get_kafka_consumer, produce_event, get_kafka_producer
from app.s3_utils import download_file_bytes, upload_file_bytes
from app.models import SessionLocal, CertificateAnalysis
from app.forensics import score_document, heatmap_b64

consumer = get_kafka_consumer("forensics-worker-group", ["ocr_completed"])
producer = get_kafka_producer()

def process_forensics(event):
    analysis_id = event["analysis_id"]
    file_key = event["file_key"]
    content_type = event["content_type"]
    ocr_text = event.get("ocr_text", "")
    
    print(f"[Forensics] Processing {analysis_id}")
    
    try:
        # Download from Minio
        file_bytes = download_file_bytes(file_key)
        
        ext = ".pdf" if content_type == "application/pdf" else ".jpg"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
            
        # Run Forensics
        score, signals, gray = score_document(tmp_path, ocr_text, content_type)
        heat = heatmap_b64(gray)
        
        if score >= 80:
            verdict = "Likely Genuine"
        elif score >= 55:
            verdict = "Needs Review"
        else:
            verdict = "Likely Fake"
            
        confidence = round(score / 100.0, 2)
        
        # Upload Heatmap to Minio
        heatmap_key = f"{analysis_id}/heatmap.png"
        if heat:
            import base64
            upload_file_bytes(heatmap_key, base64.b64decode(heat))
            
        # Update DB
        db = SessionLocal()
        analysis = db.query(CertificateAnalysis).filter(CertificateAnalysis.id == analysis_id).first()
        if analysis:
            analysis.authenticity_score = score
            analysis.verdict = verdict
            analysis.suspicious_signals = signals
            analysis.confidence = confidence
            analysis.status = "completed"
            db.commit()
        db.close()
        
        # Publish completion event
        produce_event(producer, "analysis_completed", analysis_id, {"analysis_id": analysis_id})
        print(f"[Forensics] Completed {analysis_id}")
        
    except Exception as e:
        print(f"[Forensics] Failed {analysis_id}: {e}")
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
    print("[Forensics] Worker started. Waiting for events...")
    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            print(f"[Forensics] Consumer error: {msg.error()}")
            continue
        
        event = json.loads(msg.value().decode('utf-8'))
        process_forensics(event)
