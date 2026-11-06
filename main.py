import os
import httpx
from abc import ABC, abstractmethod
from typing import Optional, List
from fastapi import FastAPI, BackgroundTasks, HTTPException, status
from pydantic import BaseModel
from datetime import datetime

# =====================================================================
# PATRÓN STRATEGY: Algoritmo de Compatibilidad (Clean Code & SOLID)
# =====================================================================
class EstrategiaMatching(ABC):
    @abstractmethod
    def calcular(self, cv_texto: str, requisitos: str, pref: dict, vacante: dict) -> float:
        pass

class EstrategiaHibridaPonderada(EstrategiaMatching):
    """Ponderación: 70% competencias técnicas del CV y 30% preferencias laborales."""
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
# PATRÓN OBSERVER: Webhook desacoplado a n8n
# =====================================================================
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "https://swipejob-n8n.onrender.com/webhook/swipe")

async def notificar_observer_n8n(payload: dict):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(N8N_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"[Observer Warning] n8n no respondió o está en suspensión: {e}")

# =====================================================================
# MODELOS PYDANTIC
# =====================================================================
class SwipeInput(BaseModel):
    id_candidato: int
    id_vacante: int
    decision: str  # 'like' o 'dislike'

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
# ENDPOINTS REST (PATRÓN FACHADA / FACADE)
# =====================================================================

# HU-RF-005, HU-RF-013 & HU-RNF-015: Swipe asíncrono
@app.post("/api/swipes")
async def registrar_swipe(datos: SwipeInput, background_tasks: BackgroundTasks):
    cursor.execute("""
        INSERT INTO matches (id_candidato, id_vacante, decision, afinidad, fecha_interaccion)
        VALUES (%s, %s, %s, NULL, CURRENT_TIMESTAMP)
        ON CONFLICT (id_candidato, id_vacante) DO UPDATE
        SET decision = EXCLUDED.decision, fecha_interaccion = CURRENT_TIMESTAMP;
    """, (datos.id_candidato, datos.id_vacante, datos.decision))
    db.commit()

    if datos.decision == "like":
        # Dispara el Observer en background hacia n8n
        background_tasks.add_task(notificar_observer_n8n, {
            "evento": "postulacion",
            "id_candidato": datos.id_candidato,
            "id_vacante": datos.id_vacante,
            "decision": datos.decision
        })

    return {"mensaje": "Swipe procesado exitosamente"}

# HU-RF-011: Centro de Notificaciones In-App
@app.get("/api/candidato/notificaciones/{id_usuario}")
async def obtener_notificaciones(id_usuario: int):
    cursor.execute("""
        SELECT id_notificacion, id_usuario, tipo, titulo, mensaje, leida, enlace, fecha_creacion
        FROM notificaciones
        WHERE id_usuario = %s
        ORDER BY fecha_creacion DESC LIMIT 30;
    """, (id_usuario,))
    columnas = [desc[0] for desc in cursor.description]
    return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]

@app.put("/api/notificaciones/{id_notificacion}/leer")
async def marcar_notificacion_leida(id_notificacion: int):
    cursor.execute("UPDATE notificaciones SET leida = TRUE WHERE id_notificacion = %s RETURNING *;", (id_notificacion,))
    db.commit()
    return {"mensaje": "Notificación marcada como leída"}

@app.put("/api/notificaciones/usuario/{id_usuario}/leer-todas")
async def marcar_todas_leidas(id_usuario: int):
    cursor.execute("UPDATE notificaciones SET leida = TRUE WHERE id_usuario = %s;", (id_usuario,))
    db.commit()
    return {"mensaje": "Todas las notificaciones fueron marcadas como leídas"}

# HU-RF-009: Agenda de Entrevistas
@app.get("/api/candidato/entrevistas/{id_candidato}")
async def obtener_entrevistas_candidato(id_candidato: int):
    cursor.execute("""
        SELECT e.*, emp.nombre as empresa_nombre
        FROM entrevistas e
        JOIN empresas emp ON e.id_empresa = emp.id_empresa
        WHERE e.id_candidato = %s
        ORDER BY e.fecha_hora ASC;
    """, (id_candidato,))
    columnas = [desc[0] for desc in cursor.description]
    return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]

@app.post("/api/empresa/entrevistas")
async def agendar_entrevista_empresa(datos: EntrevistaInput, background_tasks: BackgroundTasks):
    cursor.execute("""
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
    db.commit()
    id_nueva = cursor.fetchone()[0]

    # Genera notificación in-app en Neon
    cursor.execute("SELECT id_usuario FROM candidatos WHERE id_candidato = %s;", (datos.id_candidato,))
    row_user = cursor.fetchone()
    if row_user:
        cursor.execute("""
            INSERT INTO notificaciones (id_usuario, tipo, titulo, mensaje, enlace)
            VALUES (%s, 'entrevista', 'Nueva Entrevista Programada', %s, 'agenda');
        """, (row_user[0], f"Tienes una entrevista programada: {datos.titulo} el {datos.fecha_hora.strftime('%d/%m/%Y %H:%M')}."))
        db.commit()

    # Dispara Observer hacia n8n para enviar correo al candidato
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
async def confirmar_entrevista(id_entrevista: int):
    cursor.execute("UPDATE entrevistas SET estado = 'confirmada' WHERE id_entrevista = %s;", (id_entrevista,))
    db.commit()
    return {"mensaje": "Entrevista confirmada"}
