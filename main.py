import os
import io
import json
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from pypdf import PdfReader

app = FastAPI(title="SwipeJob API", version="3.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")

def get_db():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL no configurada en las variables de entorno.")
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al conectar con la base de datos: {str(e)}")

# Modelos Pydantic
class LoginRequest(BaseModel):
    correo: str
    password: str

class RegistroRequest(BaseModel):
    nombre: str
    correo: str
    password: str
    rol: str
    telefono: Optional[str] = ""

class PerfilUpdateRequest(BaseModel):
    id_candidato: int
    nombre: str
    telefono: str
    estudios: str
    proyeccion: str
    perfil_profesional: str

class PreferenciasRequest(BaseModel):
    id_candidato: int
    cargo_deseado: str
    ubicacion_deseada: str
    empresas_interes: str
    rango_salarial: str
    modalidad: str
    tipo_contrato: str

class VacanteRequest(BaseModel):
    id_empresa: int
    titulo: str
    descripcion: str
    requisitos: str
    salario: str
    modalidad: str
    ubicacion: str

class SwipeRequest(BaseModel):
    id_candidato: int
    id_vacante: int
    decision: str

@app.get("/")
def ruta_raiz():
    return {"status": "SwipeJob API online", "version": "3.3.0"}

# HU-01: Autenticación
@app.post("/api/login")
def login(req: LoginRequest):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id_usuario, nombre, correo, password, rol FROM usuarios WHERE correo = %s", (req.correo,))
    user = cur.fetchone()
    if not user or user["password"] != req.password:
        cur.close()
        conn.close()
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    id_candidato = None
    id_empresa = None
    if user["rol"] == "candidato":
        cur.execute("SELECT id_candidato FROM candidatos WHERE id_usuario = %s", (user["id_usuario"],))
        c = cur.fetchone()
        if c: id_candidato = c["id_candidato"]
    else:
        cur.execute("SELECT id_empresa FROM empleadores WHERE id_usuario = %s", (user["id_usuario"],))
        e = cur.fetchone()
        if e: id_empresa = e["id_empresa"]

    cur.close()
    conn.close()
    return {
        "id_usuario": user["id_usuario"],
        "nombre": user["nombre"],
        "correo": user["correo"],
        "rol": user["rol"],
        "id_candidato": id_candidato,
        "id_empresa": id_empresa
    }

# HU-01 & HU-02: Registro
@app.post("/api/registro")
def registro(req: RegistroRequest):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id_usuario FROM usuarios WHERE correo = %s", (req.correo,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="El correo ya se encuentra registrado.")

        cur.execute("""
            INSERT INTO usuarios (nombre, correo, password, rol)
            VALUES (%s, %s, %s, %s) RETURNING id_usuario;
        """, (req.nombre, req.correo, req.password, req.rol))
        user_id = cur.fetchone()["id_usuario"]

        id_candidato = None
        id_empresa = None

        if req.rol == "candidato":
            cur.execute("""
                INSERT INTO candidatos (id_usuario, telefono, perfil_profesional, ubicacion, experiencia_years, estudios, proyeccion)
                VALUES (%s, %s, 'Perfil inicial', 'Colombia', 1, 'Estudiante de Ingeniería de Sistemas', 'Desarrollador orientado a cloud y arquitecturas backend')
                RETURNING id_candidato;
            """, (user_id, req.telefono))
            id_candidato = cur.fetchone()["id_candidato"]

            cur.execute("""
                INSERT INTO preferencias (id_candidato, cargo_deseado, ubicacion_deseada, empresas_interes, rango_salarial, modalidad, tipo_contrato)
                VALUES (%s, 'Desarrollador de software', 'Medellín', 'Nutresa', '2.500.000 CP - 7.500.000 CP', 'Remoto', 'Término indefinido')
                ON CONFLICT (id_candidato) DO NOTHING;
            """, (id_candidato,))
        else:
            cur.execute("""
                INSERT INTO empresas (nombre, sector, descripcion, ubicacion_empresa, nit)
                VALUES (%s, 'Tecnología', 'Empresa Verificada', 'Colombia', %s) RETURNING id_empresa;
            """, (req.nombre, f"NIT-{user_id}"))
            id_empresa = cur.fetchone()["id_empresa"]
            cur.execute("INSERT INTO empleadores (id_usuario, id_empresa, cargo) VALUES (%s, %s, 'Reclutador')", (user_id, id_empresa))

        conn.commit()
        cur.close()
        conn.close()
        return {"id_usuario": user_id, "nombre": req.nombre, "correo": req.correo, "rol": req.rol, "id_candidato": id_candidato, "id_empresa": id_empresa}
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

# HU-02: Perfil
@app.get("/api/candidato/perfil/{id_candidato}")
def obtener_perfil(id_candidato: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id_candidato, u.nombre, u.correo, c.telefono, c.estudios, c.proyeccion, c.perfil_profesional,
               hv.url_archivo AS cv_nombre, hv.fecha_subida AS cv_fecha
        FROM candidatos c
        JOIN usuarios u ON c.id_usuario = u.id_usuario
        LEFT JOIN (
            SELECT id_candidato, url_archivo, fecha_subida FROM hojas_vida ORDER BY id_cv DESC LIMIT 1
        ) hv ON hv.id_candidato = c.id_candidato
        WHERE c.id_candidato = %s;
    """, (id_candidato,))
    perfil = cur.fetchone()
    cur.close()
    conn.close()
    return perfil or {}

@app.put("/api/candidato/perfil")
def actualizar_perfil(req: PerfilUpdateRequest):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE candidatos 
            SET telefono = %s, estudios = %s, proyeccion = %s, perfil_profesional = %s 
            WHERE id_candidato = %s RETURNING id_usuario;
        """, (req.telefono, req.estudios, req.proyeccion, req.perfil_profesional, req.id_candidato))
        user_row = cur.fetchone()
        if user_row:
            cur.execute("UPDATE usuarios SET nombre = %s WHERE id_usuario = %s", (req.nombre, user_row["id_usuario"]))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "ok", "mensaje": "Perfil actualizado exitosamente"}
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

# HU-04: Preferencias
@app.post("/api/candidato/preferencias")
def guardar_preferencias(req: PreferenciasRequest):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO preferencias (id_candidato, cargo_deseado, ubicacion_deseada, empresas_interes, rango_salarial, modalidad, tipo_contrato)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id_candidato) DO UPDATE 
        SET cargo_deseado = EXCLUDED.cargo_deseado,
            ubicacion_deseada = EXCLUDED.ubicacion_deseada,
            empresas_interes = EXCLUDED.empresas_interes,
            rango_salarial = EXCLUDED.rango_salarial,
            modalidad = EXCLUDED.modalidad,
            tipo_contrato = EXCLUDED.tipo_contrato;
    """, (req.id_candidato, req.cargo_deseado, req.ubicacion_deseada, req.empresas_interes, req.rango_salarial, req.modalidad, req.tipo_contrato))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "ok", "mensaje": "Preferencias de interés actualizadas"}

# HU-03: CV Parsing
@app.post("/api/candidato/cv")
async def subir_cv(id_candidato: int = Form(...), archivo: UploadFile = File(...)):
    conn = get_db()
    cur = conn.cursor()
    contenido = await archivo.read()
    nombre = archivo.filename
    texto = ""

    if nombre.lower().endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(contenido))
            for page in reader.pages:
                t = page.extract_text()
                if t: texto += t + " "
        except Exception:
            texto = ""
    else:
        try: texto = contenido.decode("utf-8", errors="ignore")
        except Exception: texto = ""

    texto_limpio = " ".join(texto.split())
    if not texto_limpio:
        texto_limpio = f"Hoja de vida: {nombre}. Competencias y perfil profesional técnico."

    cur.execute("INSERT INTO hojas_vida (id_candidato, url_archivo, texto_extraido, fecha_subida) VALUES (%s, %s, %s, NOW());", (id_candidato, nombre, texto_limpio))
    cur.execute("UPDATE candidatos SET perfil_profesional = %s WHERE id_candidato = %s;", (texto_limpio[:2500], id_candidato))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "success", "mensaje": f"CV '{nombre}' procesado exitosamente."}

# HU-06: Feed Condicionado por Hoja de Vida
@app.get("/api/vacantes/feed/{id_candidato}")
def feed_candidato(id_candidato: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT hv.texto_extraido, c.perfil_profesional
        FROM candidatos c
        LEFT JOIN hojas_vida hv ON hv.id_candidato = c.id_candidato
        WHERE c.id_candidato = %s ORDER BY hv.id_cv DESC LIMIT 1;
    """, (id_candidato,))
    c = cur.fetchone()
    texto_cv = f"{c.get('texto_extraido') or ''} {c.get('perfil_profesional') or ''}".lower() if c else ""

    if not texto_cv.strip() or "sin subir" in texto_cv:
        cur.close()
        conn.close()
        return {"requiere_cv": True, "vacantes": []}

    cur.execute("""
        SELECT v.*, e.nombre AS nombre_empresa
        FROM vacantes v
        JOIN empresas e ON v.id_empresa = e.id_empresa
        WHERE v.id_vacante NOT IN (
            SELECT id_vacante FROM postulaciones WHERE id_candidato = %s
        ) AND v.estado = 'activa' ORDER BY v.id_vacante DESC;
    """, (id_candidato,))
    vacantes = cur.fetchall()

    filtradas = []
    for vac in vacantes:
        req = f"{vac.get('titulo', '')} {vac.get('requisitos', '')}".lower()
        palabras = [p.strip(".,;:()") for p in req.split() if len(p.strip(".,;:()")) > 3]
        matches = [p for p in palabras if p in texto_cv]
        if len(matches) >= 1 or len(palabras) <= 2:
            filtradas.append(vac)

    cur.close()
    conn.close()
    return {"requiere_cv": False, "vacantes": filtradas}

# HU-11: Historial de Postulaciones (Portafolio)
@app.get("/api/candidato/postulaciones/{id_candidato}")
def historial_candidato(id_candidato: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id_postulacion, v.titulo, e.nombre AS empresa, p.fecha_postulacion,
               COALESCE(p.estado_proceso, 'En revisión') AS estado,
               COALESCE(m.afinidad, 78) AS afinidad
        FROM postulaciones p
        JOIN vacantes v ON p.id_vacante = v.id_vacante
        JOIN empresas e ON v.id_empresa = e.id_empresa
        LEFT JOIN matches m ON (m.id_candidato = p.id_candidato AND m.id_vacante = p.id_vacante)
        WHERE p.id_candidato = %s
        ORDER BY p.fecha_postulacion DESC;
    """, (id_candidato,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# HU-07, HU-08: Swipes y Webhook
@app.post("/api/swipes")
def procesar_swipe(req: SwipeRequest):
    conn = get_db()
    cur = conn.cursor()
    if req.decision == "like":
        cur.execute("""
            INSERT INTO postulaciones (id_candidato, id_vacante, estado, estado_proceso, fecha_postulacion)
            VALUES (%s, %s, 'Postulado', 'En revisión', NOW())
            ON CONFLICT DO NOTHING;
        """, (req.id_candidato, req.id_vacante))
        conn.commit()

        if N8N_WEBHOOK_URL:
            try:
                requests.post(N8N_WEBHOOK_URL, json={"candidato_id": req.id_candidato, "vacante_id": req.id_vacante}, timeout=5)
            except Exception:
                pass

    cur.close()
    conn.close()
    return {"status": "ok", "decision": req.decision}

# HU-05: Vacantes
@app.post("/api/vacantes")
def crear_vacante(req: VacanteRequest):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO vacantes (id_empresa, titulo, descripcion, requisitos, salario, modalidad, ubicacion, estado, fecha_publicacion)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'activa', NOW()) RETURNING id_vacante;
    """, (req.id_empresa, req.titulo, req.descripcion, req.requisitos, req.salario, req.modalidad, req.ubicacion))
    v_id = cur.fetchone()["id_vacante"]
    conn.commit()
    cur.close()
    conn.close()
    return {"id_vacante": v_id, "mensaje": "Vacante publicada"}

# HU-12: Panel de la Empresa
@app.get("/api/empresa/postulaciones/{id_empresa}")
def postulaciones_empresa(id_empresa: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id_postulacion, u.nombre AS candidato_nombre, u.correo AS candidato_correo,
               c.telefono, c.estudios, c.proyeccion, v.titulo AS vacante_titulo,
               COALESCE(m.afinidad, 82) AS afinidad, COALESCE(hv.url_archivo, 'CV_Adjunto.pdf') AS cv_url,
               p.estado_proceso
        FROM postulaciones p
        JOIN candidatos c ON p.id_candidato = c.id_candidato
        JOIN usuarios u ON c.id_usuario = u.id_usuario
        JOIN vacantes v ON p.id_vacante = v.id_vacante
        LEFT JOIN matches m ON (m.id_candidato = p.id_candidato AND m.id_vacante = p.id_vacante)
        LEFT JOIN hojas_vida hv ON hv.id_candidato = c.id_candidato
        WHERE v.id_empresa = %s
        ORDER BY afinidad DESC, p.fecha_postulacion DESC;
    """, (id_empresa,))
    res = cur.fetchall()
    cur.close()
    conn.close()
    return res
