"""Settings declarativas do Event Logger — propositalmente vazias.

O schema gerado pela docstring vira a mensagem mostrada no drawer
"Configurar" da tela de Plugins, apontando o usuário pra tela real.
"""

from pydantic import BaseModel


class Settings(BaseModel):
    """Este plugin não tem configurações.

    Para visualizar os eventos em tempo real, abra a engrenagem
    (canto superior direito) → seção **Plugins** → **Event Logger**.
    """
    pass
