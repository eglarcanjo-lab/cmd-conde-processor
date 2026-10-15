"""Processador HOP — porta de entrada.
O código foi dividido em módulos por tema (Auditoria out/26); este arquivo só reexporta
tudo, então `from processor import X` (app.py) continua igual.
"""
from proc_base import *  # noqa: F401,F403
from proc_clientes_pedidos import *  # noqa: F401,F403
from proc_cora import *  # noqa: F401,F403
from proc_analises import *  # noqa: F401,F403
from proc_cadastros import *  # noqa: F401,F403
from proc_rv import *  # noqa: F401,F403
from proc_spo_visitas import *  # noqa: F401,F403
from proc_spo_tasks import *  # noqa: F401,F403
from proc_spo_programas import *  # noqa: F401,F403
