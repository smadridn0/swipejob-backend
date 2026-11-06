import os
import io
import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from abc import ABC, abstractmethod
from typing import Optional, List
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File, Form, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pypdf import PdfReader

# =====================================================================
# 1. INICIALIZACIÓN DE LA APLICACIÓN Y CORS
# =====================================================================
app = FastAPI(
    title="SwipeJob API",
    description="Backend para plataforma de intermediación laboral SwipeJob",
    version="1.2.0"
)

# Permitir conexiones desde Netlify, localhost y cualquier origen
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conexión a Neon PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db():
    """Retorna una conexión activa a Neon PostgreSQL."""
    if not DATABASE_URL:
        raise HTTPException(
            status_code=500,
            detail="La variable de entorno DATABASE_URL no está configurada en Render."
        )
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"[Error DB Connection]: {e}")
        raise HTTPException(status_code=500, detail="Error al conectar con la base de datos Neon.")

# =====================================================================
# 2. PATRÓN STRATEGY: Algoritmo de Compatibilidad (Clean Code & SOLID)
# =====================================================================
class EstrategiaMatching(ABC):
    @abstractmethod
    def calcular(self, cv_texto: str, requisitos: str, pref: dict, vacante: dict) -> float:
        pass

class EstrategiaHibridaPonderada(EstrategiaMatching):
    """Ponderación híbrida: 70% competencias técnicas y 30% preferencias laborales."""
    def __init__(self, peso_cv: float = 0.70, peso_pref: float = 0.30):
        self.peso_cv = peso_cv
        self.peso_pref = peso_pref

    def calcular(self, cv_texto: str, requisitos: str, pref: dict, vacante: dict) -> float:
        req_list = [r.strip().lower() for r in requisitos.split(",") if r.strip()]
        afinidad_cv = 100.0
        if req_list:
            coincidencias = sum(1 for r in req_list if r in cv_texto.lower())
            afinidad_cv = (coincidencias / len(req_list)) * 100.0

        puntos_pref = 0
        if pref.get("modalidad", "").lower() == vacante.get("modalidad", "").lower():
            puntos_pref += 1
        if pref.get("ubicacion", "").lower() == vacante.get("ubicacion", "").lower():
            puntos_pref += 1
        if pref.get("tipo_contrato", "").lower() == vacante.get("tipo_contrato", "").lower():
            puntos_pref += 1
        afinidad_pref = (puntos_pref / 3.0) * 100.0

        return round((afinidad_cv * self.peso_cv) + (afinidad_pref * self.peso_pref), 2)

class ServicioScoring:
    def __init__(self, estrategia: EstrategiaMatching):
        self.estrategia = estrategia
        self.version_modelo = "v1.2-hibrida"

    def evaluar(self, cv: str, req: str, pref: dict, vac: dict) -> float:
        return self.estrategia.calcular(cv, req, pref, vac)

# =====================================================================
# 3. PATRÓN OBSERVER: Webhook asíncrono a n8n
# =====================================================================
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "https://swipejob-n8n.onrender.com/webhook/swipe")

async def notificar_observer_n8n(payload: dict):
    """Notifica al orquestador n8n en segundo plano sin congelar la respuesta del cliente."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(N8N_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"[Observer Warning] No se pudo conectar con n8n: {e}")

# =====================================================================
# 4. MODELOS PYDANTIC (Segregación de Interfaces - ISP)
# =====================================================================
class LoginInput(BaseModel):
    correo: str
    password: str

class RegistroInput(BaseModel):
    nombre: str
    correo: str
    password: str
    rol: str  # 'candidato' o 'empresa'
    telefono: Optional[str] = None

class PerfilUpdateInput(BaseModel):
    id_candidato: int
    nombre: Optional[str] = None
    telefono: Optional[str] = None
    estudios: Optional[str] = None
    proyeccion: Optional[str] = None
    perfil_profesional: Optional[str] = None

class PreferenciasInput(BaseModel):
    id_candidato: int
    cargo_deseado: Optional[str] = None
    ubicacion_deseada: Optional[str] = None
    empresas_interes: Optional[str] = None
    rango_salarial: Optional[str] = None
    modalidad: Optional[str] = None
    tipo_contrato: Optional[str] = None

class SwipeInput(BaseModel):
    id_candidato: int
    id_vacante: int
    decision: str  # 'like' o 'dislike'

class VacanteInput(BaseModel):
    id_empresa: int
    titulo: str
    descripcion: str
    requisitos: str
    salario: Optional[str] = None
    modalidad: Optional[str] = "Remoto"
    ubicacion: Optional[str] = "Colombia"

class EntrevistaInput(BaseModel):
    id_candidato: int
    id_empresa: int
    id_postulacion: Optional[int] = None
    titulo: str
    fecha_hora: datetime
    modalidad: Optional[str] = "Remoto"
    enlace_reunion: Optional[str] = None
    ubicacion: Optional[str] = None
    notas: Optional[str] = None

# =====================================================================
# 5. ENDPOINTS DE AUTENTICACIÓN Y PERFIL (12 HU PREVIAS)
# =====================================================================
@app.get("/")
def health_check():
    return {"status": "ok", "app": "SwipeJob Backend", "version": "1.2.0"}

@app.post("/api/login")
def login(datos: LoginInput):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM usuarios WHERE correo = %s AND password = %s;", (datos.correo, datos.password))
        user = cur.fetchone()
        if not user:
            conn.close()
            raise HTTPException(status_code=401, detail="Credenciales incorrectas")

        id_candidato = None
        id_empresa = None
        if user["rol"] == "candidato":
            cur.execute("SELECT id_candidato FROM candidatos WHERE id_usuario = %s;", (user["id_usuario"],))
            cand = cur.fetchone()
            if cand:
                id_candidato = cand["id_candidato"]
        elif user["rol"] == "empresa":
            cur.execute("SELECT id_empresa FROM empresas WHERE id_usuario = %s;", (user["id_usuario"],))
            emp = cur.fetchone()
            if emp:
                id_empresa = emp["id_empresa"]

    conn.close()
    return {
        "id_usuario": user["id_usuario"],
        "nombre": user["nombre"],
        "correo": user["correo"],
        "rol": user["rol"],
        "id_candidato": id_candidato,
        "id_empresa": id_empresa
    }

@app.post("/api/registro")
def registro(datos: RegistroInput):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id_usuario FROM usuarios WHERE correo = %s;", (datos.correo,))
        if cur.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="El correo ya se encuentra registrado.")

        cur.execute("""
            INSERT INTO usuarios (nombre, correo, password, rol, telefono)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_usuario;
        """, (datos.nombre, datos.correo, datos.password, datos.rol, datos.telefono))
        id_usuario = cur.fetchone()["id_usuario"]

        id_candidato = None
        id_empresa = None
        if datos.rol == "candidato":
            cur.execute("""
                INSERT INTO candidatos (id_usuario, estudios, proyeccion, perfil_profesional)
                VALUES (%s, 'Estudios por definir', 'Proyección laboral', '')
                RETURNING id_candidato;
            """, (id_usuario,))
            id_candidato = cur.fetchone()["id_candidato"]
        else:
            cur.execute("""
                INSERT INTO empresas (id_usuario, nombre, sector)
                VALUES (%s, %s, 'General')
                RETURNING id_empresa;
            """, (id_usuario, datos.nombre))
            id_empresa = cur.fetchone()["id_empresa"]

        conn.commit()
    conn.close()

    return {
        "id_usuario": id_usuario,
        "nombre": datos.nombre,
        "correo": datos.correo,
        "rol": datos.rol,
        "id_candidato": id_candidato,
        "id_empresa": id_empresa
    }

@app.get("/api/candidato/perfil/{id_candidato}")
def obtener_perfil(id_candidato: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT u.nombre, u.correo, u.telefono, c.estudios, c.proyeccion, c.perfil_profesional,
                   hv.nombre_archivo as cv_nombre
            FROM candidatos c
            JOIN usuarios u ON c.id_usuario = u.id_usuario
            LEFT JOIN hojas_vida hv ON c.id_candidato = hv.id_candidato
            WHERE c.id_candidato = %s
            ORDER BY hv.id_cv DESC LIMIT 1;
        """, (id_candidato,))
        perfil = cur.fetchone()
    conn.close()
    return perfil or {}

@app.put("/api/candidato/perfil")
def actualizar_perfil(datos: PerfilUpdateInput):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id_usuario FROM candidatos WHERE id_candidato = %s;", (datos.id_candidato,))
        cand = cur.fetchone()
        if not cand:
            conn.close()
            raise HTTPException(status_code=404, detail="Candidato no encontrado")

        cur.execute("""
            UPDATE usuarios 
            SET nombre = COALESCE(%s, nombre), telefono = COALESCE(%s, telefono)
            WHERE id_usuario = %s;
        """, (datos.nombre, datos.telefono, cand["id_usuario"]))

        cur.execute("""
            UPDATE candidatos
            SET estudios = COALESCE(%s, estudios),
                proyeccion = COALESCE(%s, proyeccion),
                perfil_profesional = COALESCE(%s, perfil_profesional)
            WHERE id_candidato = %s;
        """, (datos.estudios, datos.proyeccion, datos.perfil_profesional, datos.id_candidato))

        conn.commit()
    conn.close()
    return {"mensaje": "Perfil actualizado exitosamente"}

@app.post("/api/candidato/cv")
async def subir_cv(id_candidato: int = Form(...), archivo: UploadFile = File(...)):
    contenido = await archivo.read()
    texto_extraido = ""

    if archivo.filename.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(contenido))
            texto_extraido = "\n".join([page.extract_text() or "" for page in reader.pages])
        except Exception:
            texto_extraido = "Error al extraer PDF"
    else:
        try:
            texto_extraido = contenido.decode("utf-8", errors="ignore")
        except Exception:
            texto_extraido = "Texto plano no procesable"

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO hojas_vida (id_candidato, nombre_archivo, texto_extraido)
            VALUES (%s, %s, %s);
        """, (id_candidato, archivo.filename, texto_extraido))
        conn.commit()
    conn.close()

    return {"mensaje": f"Hoja de vida '{archivo.filename}' cargada y procesada exitosamente."}

@app.post("/api/candidato/preferencias")
def guardar_preferencias(datos: PreferenciasInput):
    conn = get_db()
    with conn.cursor() as cur:
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
        """, (datos.id_candidato, datos.cargo_deseado, datos.ubicacion_deseada, datos.empresas_interes, datos.rango_salarial, datos.modalidad, datos.tipo_contrato))
        conn.commit()
    conn.close()
    return {"mensaje": "Preferencias guardadas exitosamente"}

@app.get("/api/vacantes/feed/{id_candidato}")
def feed_vacantes(id_candidato: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id_cv FROM hojas_vida WHERE id_candidato = %s LIMIT 1;", (id_candidato,))
        if not cur.fetchone():
            conn.close()
            return {"requiere_cv": True, "vacantes": []}

        cur.execute("""
            SELECT v.id_vacante, v.titulo, v.descripcion, v.requisitos, v.salario, v.modalidad, v.ubicacion,
                   e.nombre as nombre_empresa
            FROM vacantes v
            JOIN empresas e ON v.id_empresa = e.id_empresa
            WHERE v.id_vacante NOT IN (
                SELECT id_vacante FROM matches WHERE id_candidato = %s
            )
            ORDER BY v.fecha_publicacion DESC;
        """, (id_candidato,))
        vacantes = cur.fetchall()
    conn.close()
    return {"requiere_cv": False, "vacantes": vacantes}

@app.get("/api/candidato/postulaciones/{id_candidato}")
def historial_postulaciones(id_candidato: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m.id_match as id_postulacion, v.titulo, emp.nombre as empresa,
                   COALESCE(m.decision, 'postulado') as estado,
                   COALESCE(m.afinidad, 90) as afinidad,
                   m.fecha_interaccion as fecha_postulacion
            FROM matches m
            JOIN vacantes v ON m.id_vacante = v.id_vacante
            JOIN empresas emp ON v.id_empresa = emp.id_empresa
            WHERE m.id_candidato = %s AND m.decision = 'like'
            ORDER BY m.fecha_interaccion DESC;
        """, (id_candidato,))
        postulaciones = cur.fetchall()
    conn.close()
    return postulaciones

@app.post("/api/vacantes")
def crear_vacante(datos: VacanteInput):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO vacantes (id_empresa, titulo, descripcion, requisitos, salario, modalidad, ubicacion)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id_vacante;
        """, (datos.id_empresa, datos.titulo, datos.descripcion, datos.requisitos, datos.salario, datos.modalidad, datos.ubicacion))
        conn.commit()
    conn.close()
    return {"mensaje": "Vacante publicada exitosamente"}

@app.get("/api/empresa/postulaciones/{id_empresa}")
def postulaciones_empresa(id_empresa: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m.id_match as id_postulacion, u.nombre as candidato_nombre,
                   COALESCE(m.afinidad, 92) as afinidad,
                   v.titulo as vacante_titulo, c.estudios,
                   COALESCE(hv.nombre_archivo, 'CV Adjunto') as cv_url,
                   c.id_candidato
            FROM matches m
            JOIN vacantes v ON m.id_vacante = v.id_vacante
            JOIN candidatos c ON m.id_candidato = c.id_candidato
            JOIN usuarios u ON c.id_usuario = u.id_usuario
            LEFT JOIN hojas_vida hv ON c.id_candidato = hv.id_candidato
            WHERE v.id_empresa = %s AND m.decision = 'like'
            ORDER BY m.fecha_interaccion DESC;
        """, (id_empresa,))
        postulaciones = cur.fetchall()
    conn.close()
    return postulaciones

# =====================================================================
# 6. ENDPOINTS DEL SPRINT (OBSERVER, NOTIFICACIONES Y AGENDA)
# =====================================================================

# HU-RF-005 & HU-RNF-015: Swipe Asíncrono con Observer
@app.post("/api/swipes")
async def registrar_swipe(datos: SwipeInput, background_tasks: BackgroundTasks):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO matches (id_candidato, id_vacante, decision, afinidad, fecha_interaccion)
            VALUES (%s, %s, %s, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT (id_candidato, id_vacante) DO UPDATE
            SET decision = EXCLUDED.decision, fecha_interaccion = CURRENT_TIMESTAMP;
        """, (datos.id_candidato, datos.id_vacante, datos.decision))
        conn.commit()
    conn.close()

    if datos.decision == "like":
        background_tasks.add_task(notificar_observer_n8n, {
            "evento": "postulacion",
            "id_candidato": datos.id_candidato,
            "id_vacante": datos.id_vacante,
            "decision": datos.decision
        })

    return {"mensaje": "Swipe procesado exitosamente"}

# HU-RF-011: Centro de Notificaciones In-App
@app.get("/api/candidato/notificaciones/{id_usuario}")
def obtener_notificaciones(id_usuario: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id_notificacion, id_usuario, tipo, titulo, mensaje, leida, enlace, fecha_creacion
            FROM notificaciones
            WHERE id_usuario = %s
            ORDER BY fecha_creacion DESC LIMIT 30;
        """, (id_usuario,))
        notifs = cur.fetchall()
    conn.close()
    return notifs

@app.put("/api/notificaciones/{id_notificacion}/leer")
def marcar_notificacion_leida(id_notificacion: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("UPDATE notificaciones SET leida = TRUE WHERE id_notificacion = %s RETURNING *;", (id_notificacion,))
        conn.commit()
    conn.close()
    return {"mensaje": "Notificación marcada como leída"}

@app.put("/api/notificaciones/usuario/{id_usuario}/leer-todas")
def marcar_todas_leidas(id_usuario: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("UPDATE notificaciones SET leida = TRUE WHERE id_usuario = %s;", (id_usuario,))
        conn.commit()
    conn.close()
    return {"mensaje": "Todas las notificaciones fueron marcadas como leídas"}

# HU-RF-009: Agenda y Entrevistas
@app.get("/api/candidato/entrevistas/{id_candidato}")
def obtener_entrevistas_candidato(id_candidato: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.*, emp.nombre as empresa_nombre
            FROM entrevistas e
            JOIN empresas emp ON e.id_empresa = emp.id_empresa
            WHERE e.id_candidato = %s
            ORDER BY e.fecha_hora ASC;
        """, (id_candidato,))
        entrevistas = cur.fetchall()
    conn.close()
    return entrevistas

@app.post("/api/empresa/entrevistas")
async def agendar_entrevista_empresa(datos: EntrevistaInput, background_tasks: BackgroundTasks):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO entrevistas (
                id_postulacion, id_candidato, id_empresa, titulo, fecha_hora, 
                duracion_minutos, modalidad, enlace_reunion, ubicacion, notas, estado
            ) VALUES (%s, %s, %s, %s, %s, 45, %s, %s, %s, %s, 'programada')
            RETURNING id_entrevista;
        """, (
            datos.id_postulacion, datos.id_candidato, datos.id_empresa,
            datos.titulo, datos.fecha_hora, datos.modalidad,
            datos.enlace_reunion, datos.ubicacion, datos.notas
        ))
        id_nueva = cur.fetchone()["id_entrevista"]

        # Generar notificación in-app en Neon
        cur.execute("SELECT id_usuario FROM candidatos WHERE id_candidato = %s;", (datos.id_candidato,))
        row_user = cur.fetchone()
        if row_user:
            cur.execute("""
                INSERT INTO notificaciones (id_usuario, tipo, titulo, mensaje, enlace)
                VALUES (%s, 'entrevista', 'Nueva Entrevista Programada', %s, 'agenda');
            """, (row_user["id_usuario"], f"Tienes una entrevista programada: {datos.titulo} el {datos.fecha_hora.strftime('%d/%m/%Y %H:%M')}."))

        conn.commit()
    conn.close()

    # Disparar evento al Observer (n8n) para enviar correo
    background_tasks.add_task(notificar_observer_n8n, {
        "evento": "entrevista_agendada",
        "id_candidato": datos.id_candidato,
        "id_empresa": datos.id_empresa,
        "titulo": datos.titulo,
        "fecha_hora": datos.fecha_hora.strftime('%d/%m/%Y %H:%M'),
        "modalidad": datos.modalidad,
        "enlace_reunion": datos.enlace_reunion or "Por coordinar"
    })

    return {"mensaje": "Entrevista agendada exitosamente", "id_entrevista": id_nueva}

@app.put("/api/entrevistas/{id_entrevista}/confirmar")
def confirmar_entrevista(id_entrevista: int):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("UPDATE entrevistas SET estado = 'confirmada' WHERE id_entrevista = %s;", (id_entrevista,))
        conn.commit()
    conn.close()
    return {"mensaje": "Entrevista confirmada"}
