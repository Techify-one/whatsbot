"""Notificações — plugin somente de UI.

Expõe uma tela (screen) Preact que lê/grava as preferências de notificação
deste navegador/dispositivo (localStorage). A lógica de disparo (contador na
aba, notificação do navegador, som) continua no core, em
``web/static/js/utils/notifications.js`` — este plugin apenas oferece os
controles que antes viviam na seção "Notificações" das Configurações.

Por ser puramente client-side, não há ``entry`` (events/filters/routes/settings)
nem migrations.
"""
