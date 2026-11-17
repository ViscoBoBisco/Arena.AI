# mod-vault-terminal

Server half of the [VaultTerminal example](../../README.md). Reference code: it shows the wire
format, the receive hook and the send path, and is not built by this repository.

Expected layout in an AzerothCore source tree:

```
modules/mod-vault-terminal/
├── conf/mod-vault-terminal.conf.dist
└── src/
    ├── VaultTerminal.cpp
    └── mod-vault-terminal_loader.cpp
```

Configure with CMake so the module is picked up (`-DMODULES=static` or `dynamic`, per your core's
module docs), rebuild, and the loader registers `Addmod_vault_terminalScripts()`.

## What it depends on, and why that is verified

| Dependency | Where it was checked |
|---|---|
| `PlayerScript::OnPlayerBeforeSendChatMessage(Player*, uint32&, uint32&, std::string&)` | `ScriptDefines/PlayerScript.h` (hook `PLAYERHOOK_ON_BEFORE_SEND_CHAT_MESSAGE`) |
| `ChatHandler::BuildChatPacket(data, CHAT_MSG_WHISPER, LANG_ADDON, player, player, text)` | `Chat.cpp::AddonChannelCommandHandler::Send()` |
| `AddonChannel = 1` gates the channel | `worldserver.conf.dist`, `Handlers/ChatHandler.cpp` |
| The core's own prefix/opcodes (`AzerothCore\t`, `p/h/i`, replies `a/o/f/m`) | `Chat.cpp::AddonChannelCommandHandler::ParseCommands()` |
| The loader symbol must be `Addmod_vault_terminalScripts()` | `modules/CMakeLists.txt` → `ConfigureScriptLoader`: `string(REGEX REPLACE - "_" …)` then `Add${name}Scripts()` |
| Sources are globbed from `src/`, so no per-module `CMakeLists.txt` is needed | `modules/CMakeLists.txt` → `CollectSourceFiles(${MODULE_SOURCE_PATH} …)` |
| `-DMODULES=static|dynamic` selects linkage | `modules/CMakeLists.txt`, `GetModuleSourceList` + `MODULES MATCHES` |

If your fork renames the hook, the protocol still works — only the interception point moves. Keep the
`msg.clear()`: it is what stops the message being relayed to other players after you have claimed it.
