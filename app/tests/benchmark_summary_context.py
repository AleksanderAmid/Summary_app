"""Manual local-model capacity check using synthetic Swedish text only."""
import argparse, json, sys, time, traceback
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import summarization, summary_context
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True, help="Directory for synthetic source and result files")
root=parser.parse_args().output
root.mkdir(parents=True, exist_ok=True)
blocks=[]
for n in range(640):
    blocks.append(f"Historisk anteckning {n+1}. [NAME_PATIENT_MALE_01] kom för en rutinmässig uppföljning. "
                  "Allmäntillståndet var gott vid besöket. Inga nytillkomna symtom beskrevs. "
                  "Information om kontaktvägar lämnades. Den administrativa registreringen avslutades. "
                  "Anteckningen innehåller inga nya läkemedelsordinationer eller provsvar.")
blocks.insert(0, "AKTUELL SÄKERHETSUPPGIFT: [NAME_PATIENT_MALE_01] har bekräftad penicillinallergi med tidigare anafylaxi. Uppgiften är fortfarande aktuell.")
blocks.insert(len(blocks)//2, "AKTUELL LÄKEMEDELSÄNDRING: Metformin 500 mg två gånger dagligen har satts ut på grund av gastrointestinala biverkningar. Läkemedlet ska beskrivas som utsatt, inte som pågående behandling.")
blocks.append("AKTUELL UPPFÖLJNINGSPLAN: Återbesök på diabetesmottagningen om två veckor för kontroll av HbA1c och bedömning av fortsatt behandling. Besöket är planerat och ännu inte genomfört.")
source="\n\n".join(blocks)
(root/'synthetic-long-record.txt').write_text(source, encoding="utf-8")
print(json.dumps({"source_chars":len(source),"source_bytes":len(source.encode()),"units":len(summarization.evidence_units(source))}), flush=True)
started=time.perf_counter()
try:
    result=summarization.summarize(source, progress=lambda message:print(message,flush=True))
    result["benchmark"]={"synthetic":True,"stage":"summary only, pseudonymised synthetic source", "elapsed_seconds":round(time.perf_counter()-started,2)}
    (root/'long-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"telemetry":result["telemetry"],"summary":result["summary"],"warnings":result["quality_warnings"],"uncertainties":result["uncertainties"]},ensure_ascii=False,indent=2),flush=True)
except Exception:
    (root/'long-error.txt').write_text(traceback.format_exc(),encoding="utf-8")
    raise
