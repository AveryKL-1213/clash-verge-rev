pub const ENABLED: bool = cfg!(feature = "local-build");

pub fn ensure_binary_updates_allowed() -> anyhow::Result<()> {
    anyhow::ensure!(
        !ENABLED,
        "This local build cannot install downloaded app or core updates. Review and rebuild the pinned sources locally."
    );
    Ok(())
}

pub fn ensure_core_allowed(core: &str) -> anyhow::Result<()> {
    anyhow::ensure!(
        !ENABLED || core == "verge-mihomo",
        "This local build only supports its bundled stable Mihomo core."
    );
    Ok(())
}

pub fn has_valid_controller_secret(config: &serde_yaml_ng::Mapping) -> bool {
    config
        .get("secret")
        .and_then(serde_yaml_ng::Value::as_str)
        .is_some_and(|secret| !secret.trim().is_empty() && secret.trim() != "set-your-secret")
}

pub fn restrict_control_plane(mut config: serde_yaml_ng::Mapping) -> serde_yaml_ng::Mapping {
    if !ENABLED {
        return config;
    }

    for key in ["external-controller", "external-controller-tls"] {
        if let Some(address) = config.get(key).and_then(serde_yaml_ng::Value::as_str)
            && !address.is_empty()
        {
            let normalized = match address.parse::<std::net::SocketAddr>() {
                Ok(mut socket) => {
                    if !socket.ip().is_loopback() {
                        if key == "external-controller-tls" {
                            config.insert(key.into(), "".into());
                            continue;
                        }
                        socket.set_ip(std::net::Ipv4Addr::LOCALHOST.into());
                    }
                    socket.to_string()
                }
                Err(_) => std::string::String::new(),
            };
            config.insert(key.into(), normalized.into());
        }
    }

    if !has_valid_controller_secret(&config) {
        for key in ["external-controller", "external-controller-tls"] {
            if config.contains_key(key) {
                config.insert(key.into(), "".into());
            }
        }
    }

    let mut cors = serde_yaml_ng::Mapping::new();
    cors.insert("allow-private-network".into(), false.into());
    cors.insert(
        "allow-origins".into(),
        vec![
            "tauri://localhost",
            "http://tauri.localhost",
            #[cfg(feature = "verge-dev")]
            "http://localhost:3000",
        ]
        .into(),
    );
    config.insert("external-controller-cors".into(), cors.into());
    config
}

#[cfg(all(test, feature = "local-build"))]
mod tests {
    use super::{ensure_binary_updates_allowed, ensure_core_allowed, restrict_control_plane};

    #[test]
    fn downloaded_updates_and_alpha_selection_are_rejected() {
        assert!(ensure_binary_updates_allowed().is_err());
        assert!(ensure_core_allowed("verge-mihomo-alpha").is_err());
        assert!(ensure_core_allowed("verge-mihomo").is_ok());
    }

    #[test]
    fn controller_restrictions_preserve_lan_gateway_settings() -> anyhow::Result<()> {
        let config: serde_yaml_ng::Mapping = serde_yaml_ng::from_str(
            "external-controller: 0.0.0.0:9097\nexternal-controller-tls: '[::]:9098'\nexternal-controller-unix: /private/tmp/test.sock\nsecret: valid-local-controller-secret\nallow-lan: true\nbind-address: '*'\ntun: {enable: true, dns-hijack: ['any:53']}\ndns: {listen: '0.0.0.0:53'}\nexternal-controller-cors: {allow-origins: ['*']}",
        )?;
        let hardened = restrict_control_plane(config.clone());
        for key in ["allow-lan", "bind-address", "tun", "dns", "external-controller-unix"] {
            assert_eq!(hardened.get(key), config.get(key));
        }
        assert_eq!(
            hardened
                .get("external-controller")
                .and_then(serde_yaml_ng::Value::as_str),
            Some("127.0.0.1:9097")
        );
        assert_eq!(
            hardened
                .get("external-controller-tls")
                .and_then(serde_yaml_ng::Value::as_str),
            Some("")
        );
        assert_ne!(
            hardened.get("external-controller-cors"),
            config.get("external-controller-cors")
        );
        Ok(())
    }

    #[test]
    fn disabled_or_invalid_controllers_never_gain_a_listener() {
        let mut config = serde_yaml_ng::Mapping::new();
        config.insert("external-controller".into(), "".into());
        config.insert("external-controller-tls".into(), "invalid".into());
        let hardened = restrict_control_plane(config);
        for key in ["external-controller", "external-controller-tls"] {
            assert_eq!(hardened.get(key).and_then(serde_yaml_ng::Value::as_str), Some(""));
        }
        let empty = restrict_control_plane(serde_yaml_ng::Mapping::new());
        assert!(!empty.contains_key("external-controller"));
        assert!(!empty.contains_key("external-controller-tls"));
    }

    #[test]
    fn invalid_secrets_disable_tcp_without_disrupting_ipc_or_gateway() -> anyhow::Result<()> {
        for secret in [
            "",
            "secret: null",
            "secret: false",
            "secret: 1234",
            "secret: ''",
            "secret: '   '",
            "secret: set-your-secret",
        ] {
            let config: serde_yaml_ng::Mapping = serde_yaml_ng::from_str(&format!(
                "{secret}\nexternal-controller: 127.0.0.1:9097\nexternal-controller-tls: '[::1]:9098'\nexternal-controller-unix: /private/tmp/test.sock\nallow-lan: true\ntun: {{enable: true}}\ndns: {{listen: '0.0.0.0:53'}}"
            ))?;
            let hardened = restrict_control_plane(config.clone());
            for key in ["external-controller", "external-controller-tls"] {
                assert_eq!(
                    hardened.get(key).and_then(serde_yaml_ng::Value::as_str),
                    Some(""),
                    "accepted {secret:?}"
                );
            }
            for key in ["external-controller-unix", "allow-lan", "tun", "dns"] {
                assert_eq!(hardened.get(key), config.get(key));
            }
        }
        Ok(())
    }
}
