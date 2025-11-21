# This file defines local variables to construct the complex configuration object
# required by the networking module. It includes the complete set of security
# rules for Security Lists and Network Security Groups required for a functional OKE cluster.

locals {

  network_configuration = {
    default_compartment_id = var.compartment_id
    default_enable_cis_checks = false
    network_configuration_categories = {
      production = {
        category_freeform_tags = {
          "vision-sub-environment" = "prod"
        }
        vcns = {
          SIMPLE-VCN-KEY = {
            display_name                     = "vcn-simple"
            is_ipv6enabled                   = false
            is_oracle_gua_allocation_enabled = false
            cidr_blocks                      = ["10.0.0.0/18"],
            dns_label                        = "vcnsimple"
            is_create_igw                    = false
            is_attach_drg                    = false
            block_nat_traffic                = false

            security_lists = {

              SECLIST-LB-KEY = {
                display_name = "sl-lb"

                egress_rules = [
                  {
                    description = "egress to 0.0.0.0/0 over ALL protocols"
                    stateless   = false
                    protocol    = "ALL"
                    dst         = "0.0.0.0/0"
                    dst_type    = "CIDR_BLOCK"
                  }
                ]

                ingress_rules = [
                  {
                    description  = "ingress from 0.0.0.0/0 over TCP22"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "0.0.0.0/0"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 22
                    dst_port_max = 22
                  },
                  {
                    description  = "ingress from 0.0.0.0/0 over TCP443"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "0.0.0.0/0"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 443
                    dst_port_max = 443
                  },
                  {
                    description  = "ingress from 0.0.0.0/0 over TCP80"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "0.0.0.0/0"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 80
                    dst_port_max = 80
                  }
                ]
              },

              SECLIST-APP-KEY = {
                display_name = "sl-app"

                egress_rules = [
                  {
                    description = "egress to 0.0.0.0/0 over TCP"
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "0.0.0.0/0"
                    dst_type    = "CIDR_BLOCK"
                  }
                ]

                ingress_rules = [
                  {
                    description  = "ingress from 10.0.3.0/24 over TCP22"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "10.0.3.0/24"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 22
                    dst_port_max = 22
                  },
                  {
                    description  = "ingress from 10.0.3.0/24 over HTTP8080"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "10.0.3.0/24"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 8080
                    dst_port_max = 8080
                  },
                  {
                    description  = "ingress from 10.0.2.0/24 over HTTP8080"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "10.0.2.0/24"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 8080
                    dst_port_max = 8080
                  },
                  {
                    description  = "ingress from 10.0.3.0/24 over HTTP8443"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "10.0.2.0/24"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 8443
                    dst_port_max = 8443
                  }
                ]
              }
              SECLIST-DB-KEY = {
                display_name = "sl-db"

                egress_rules = [
                  {
                    description = "egress to 0.0.0.0/0 over TCP"
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "0.0.0.0/0"
                    dst_type    = "CIDR_BLOCK"
                  }
                ]

                ingress_rules = [
                  {
                    description = "ingress from 10.0.2.0/24 over TCP22"
                    stateless   = false
                    protocol    = "TCP"
                    src         = "10.0.2.0/24"
                    src_type    = "CIDR_BLOCK"
                  },
                  {
                    description  = "ingress from 10.0.2.0/24 over TCP:1521"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "10.0.2.0/24"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 1521
                    dst_port_max = 1521
                  },
                  {
                    description  = "ingress from 10.0.2.0/24 over TCP:3306"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "10.0.2.0/24"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 3306
                    dst_port_max = 3306
                  }
                ]
              }
            }

            route_tables = {
              RT-01-KEY = {
                display_name = "rt-01"
                route_rules = {
                  internet_route = {
                    network_entity_key = "IGW-KEY"
                    description        = "Route for internet access"
                    destination        = "0.0.0.0/0"
                    destination_type   = "CIDR_BLOCK"
                  }
                }
              }
              RT-02-KEY = {
                display_name = "rt-02-prod-vcn-01"
                route_rules = {
                  sgw-route = {
                    network_entity_key = "SGW-KEY"
                    description        = "Route for sgw"
                    destination        = "objectstorage"
                    destination_type   = "SERVICE_CIDR_BLOCK"
                  },
                  natgw-route = {
                    network_entity_key = "NATGW-KEY"
                    description        = "Route for internet access via NAT GW"
                    destination        = "0.0.0.0/0"
                    destination_type   = "CIDR_BLOCK"
                  }
                }
              }
            }

            subnets = {
              PUBLIC-LB-SUBNET-KEY = {
                cidr_block                 = "10.0.3.0/24"
                dhcp_options_key           = "default_dhcp_options"
                display_name               = "sub-public-lb"
                dns_label                  = "publiclb"
                ipv6cidr_blocks            = []
                prohibit_internet_ingress  = false
                prohibit_public_ip_on_vnic = false
                route_table_key            = "RT-01-KEY"
                security_list_keys         = ["SECLIST-LB-KEY"]
              }
              PRIVATE-APP-SUBNET-KEY = {
                cidr_block                 = "10.0.2.0/24"
                dhcp_options_key           = "default_dhcp_options"
                display_name               = "sub-private-app"
                dns_label                  = "privateapp"
                ipv6cidr_blocks            = []
                prohibit_internet_ingress  = true
                prohibit_public_ip_on_vnic = true
                route_table_key            = "RT-02-KEY"
                security_list_keys         = ["SECLIST-APP-KEY"]
              }
              PRIVATE-DB-SUBNET-KEY = {
                cidr_block                 = "10.0.1.0/24"
                dhcp_options_key           = "default_dhcp_options"
                display_name               = "sub-private-db"
                dns_label                  = "privatedb"
                ipv6cidr_blocks            = []
                prohibit_internet_ingress  = true
                prohibit_public_ip_on_vnic = true
                route_table_id             = null
                route_table_key            = "RT-02-KEY"
                security_list_keys         = ["SECLIST-DB-KEY"]
              }
            }

            network_security_groups = {

              NSG-LB-KEY = {
                display_name = "nsg-lb"
                egress_rules = {
                  anywhere = {
                    description = "egress to 0.0.0.0/0 over TCP"
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "0.0.0.0/0"
                    dst_type    = "CIDR_BLOCK"
                  }
                }

                ingress_rules = {
                  ssh_22 = {
                    description  = "ingress from 0.0.0.0/0 over TCP22"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "0.0.0.0/0"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 22
                    dst_port_max = 22
                  },
                  http_443 = {
                    description  = "ingress from 0.0.0.0/0 over https:443"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "0.0.0.0/0"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 443
                    dst_port_max = 443
                  },
                  http_80 = {
                    description  = "ingress from 0.0.0.0/0 over https:80"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "0.0.0.0/0"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 80
                    dst_port_max = 80
                  }
                }
              },

              NSG-APP-KEY = {
                display_name = "nsg-app"
                egress_rules = {
                  anywhere = {
                    description = "egress to 0.0.0.0/0 over TCP"
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "0.0.0.0/0"
                    dst_type    = "CIDR_BLOCK"
                  }
                }

                ingress_rules = {
                  ssh_22 = {
                    description  = "ingress from 0.0.0.0/0 over TCP22"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-LB-KEY"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 22
                    dst_port_max = 22
                  }

                  http_8080 = {
                    description  = "ingress from 0.0.0.0/0 over HTTP8080"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-LB-KEY"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 8080
                    dst_port_max = 8080
                  }

                  http_8443 = {
                    description  = "ingress from 0.0.0.0/0 over HTTP8443"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-LB-KEY"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 8443
                    dst_port_max = 8443
                  }
                }
              }

              NSG-DB-KEY = {
                display_name = "nsg-db"
                egress_rules = {
                  anywhere = {
                    description = "egress to 0.0.0.0/0 over TCP"
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "0.0.0.0/0"
                    dst_type    = "CIDR_BLOCK"
                  }
                }

                ingress_rules = {
                  ssh_22 = {
                    description  = "ingress from 0.0.0.0/0 over TCP22"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-APP-KEY"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 22
                    dst_port_max = 22
                  }

                  http_8080 = {
                    description  = "ingress from 0.0.0.0/0 over TCP:1521"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-APP-KEY"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 1521
                    dst_port_max = 1521
                  }
                }
              }
            }

            vcn_specific_gateways = {
              internet_gateways = {
                IGW-KEY = {
                  enabled      = true
                  display_name = "igw-prod-vcn"
                }
              }
              nat_gateways = {
                NATGW-KEY = {
                  block_traffic = false
                  display_name  = "natgw-prod-vcn"
                }
              }
              service_gateways = {
                SGW-KEY = {
                  display_name = "sgw-prod-vcn"
                  services     = "objectstorage"
                }
              }
            }
          }
        }
        non_vcn_specific_gateways = {
          l7_load_balancers = {
            EXAMPLE-011_LB_KEY = {
              compartment_id              = null,
              display_name                = "example-01-tst"
              shape                       = "flexible"
              subnet_ids                  = null,
              subnet_keys                 = ["PUBLIC-LB-SUBNET-KEY"],
              defined_tags                = null,
              freeform_tags               = null,
              ip_mode                     = "IPV4",
              is_private                  = false,
              network_security_group_keys = ["NSG-LB-KEY"],
              reserved_ips_ids            = null,
              reserved_ips_keys           = null,
              shape_details = {
                maximum_bandwidth_in_mbps = 100,
                minimum_bandwidth_in_mbps = 10
              }
              backend_sets = {
                EXAMPLE-01-LB-BCK-END-SET-01 = {
                  health_checker = {
                    protocol            = "HTTP",
                    interval_ms         = 10000,
                    is_force_plain_text = true,
                    port                = 8080,
                    retries             = 3,
                    return_code         = 200,
                    timeout_in_millis   = 3000,
                    url_path            = "/"
                  }
                  name   = "backend-set-01",
                  policy = "LEAST_CONNECTIONS",
                  lb_cookie_session_persistence_configuration = {
                    cookie_name        = "example_cookie",
                    disable_fallback   = false,
                    domain             = "Set-cookie",
                    is_http_only       = true,
                    is_secure          = false,
                    max_age_in_seconds = 3600,
                    path               = "/",
                  }
                  backends = {
                    EXAMPLE-01-LB-BCK-END-SET-01-BE-01 = {
                      ip_address = "10.0.2.254",
                      port       = 8080,
                    }
                  }
                },
                EXAMPLE-01-LB-BCK-END-SET-02 = {
                  health_checker = {
                    protocol            = "HTTP",
                    interval_ms         = 10000,
                    is_force_plain_text = true,
                    port                = 8443,
                    retries             = 3,
                    return_code         = 200,
                    timeout_in_millis   = 3000,
                    url_path            = "/"
                  }
                  name   = "backend-set-02",
                  policy = "LEAST_CONNECTIONS",
                  lb_cookie_session_persistence_configuration = {
                    cookie_name        = "example_cookie",
                    disable_fallback   = false,
                    domain             = "Set-cookie",
                    is_http_only       = true,
                    is_secure          = false,
                    max_age_in_seconds = 3600,
                    path               = "/",
                  }
                  backends = {
                    EXAMPLE-01-LB-BCK-END-SET-02-BE-01 = {
                      ip_address = "10.0.2.254",
                      port       = 8443,
                    }
                  }
                }
              }
              cipher_suites = {
                EXAMPLE-01-LB-CIPHER-SUITE-01-KEY = {
                  name = "cipher_suite_01",
                  ciphers = [
                    "ECDHE-RSA-AES256-GCM-SHA384",
                    "ECDHE-ECDSA-AES256-GCM-SHA384",
                    "ECDHE-RSA-AES128-GCM-SHA256"
                  ]
                }
              }
              path_route_sets = {
                EXMPL_01_PATH_ROUTE_SET_01_KEY = {
                  name = "path_route_set_01",
                  path_routes = {
                    DEFAULT-KEY = {
                      backend_set_key = "EXAMPLE-01-LB-BCK-END-SET-01",
                      path            = "/"
                      path_match_type = {
                        match_type = "EXACT_MATCH"
                      }
                    }
                    CUSTOM-KEY = {
                      backend_set_key = "EXAMPLE-01-LB-BCK-END-SET-01",
                      path            = "/example/video/123"
                      path_match_type = {
                        match_type = "EXACT_MATCH"
                      }
                    }
                  }
                }
              }
              host_names = {
                LB1-HOSTNAME-1-KEY = {
                  hostname = "lb1test1.com",
                  name     = "lb1test1"
                }
                LB1-HOSTNAME-2-KEY = {
                  hostname = "lb1test2.com",
                  name     = "lb1test2"
                }
              }
              routing_policies = {
                LB1-ROUTE-POLICY-1-KEY = {
                  condition_language_version = "V1",
                  name                       = "example_routing_rules",
                  rules = {
                    HR-RULE-KEY = {
                      name      = "HR_mobile_user_rule"
                      condition = "all(http.request.headers[(i 'user-agent')] eq (i 'mobile'), http.request.url.query[(i 'department')] eq (i 'HR'))",
                      actions = {
                        ACTION-1-KEY = {
                          backend_set_key = "EXAMPLE-01-LB-BCK-END-SET-01",
                          name            = "FORWARD_TO_BACKENDSET",
                        }
                      }
                    }
                    DOCUMENTS-RULE-KEY = {
                      name      = "Documents_rule"
                      condition = "any(http.request.url.path eq (i '/documents'), http.request.headers[(i 'host')] eq (i 'doc.myapp.com'))",
                      actions = {
                        ACTION-1-KEY = {
                          backend_set_key = "EXAMPLE-01-LB-BCK-END-SET-01",
                          name            = "FORWARD_TO_BACKENDSET",
                        }
                      }
                    }
                  }
                }
              }
              rule_sets = {
                LB1-RULE-SET-1-KEY = {
                  name = "example_rule_set",
                  items = {
                    ITEM-1-KEY = {
                      action = "ADD_HTTP_REQUEST_HEADER",
                      header = "example_header_name",
                      value  = "example_value"
                    }
                    ITEM-2-KEY = {
                      action = "EXTEND_HTTP_REQUEST_HEADER_VALUE",
                      header = "example_header_name2",
                      value  = "example_value",
                      prefix : "example_prefix_value",
                      suffix : "example_suffix_value"
                    }
                  }
                }
              }
              certificates = {
              }
              listeners = {
                LB1-LSNR-1-80 = {
                  default_backend_set_key = "EXAMPLE-01-LB-BCK-END-SET-01",
                  name                    = "lb1-lsnr1-80",
                  port                    = "80",
                  protocol                = "HTTP",
                  connection_configuration = {
                    idle_timeout_in_seconds = 1200,
                  },
                }
                  LB1-LSNR-1-443 = {
                    default_backend_set_key = "EXAMPLE-01-LB-BCK-END-SET-02",
                    name                    = "lb1-lsnr1-443",
                    port                    = "443",
                    protocol                = "HTTP",
                    connection_configuration = {
                      idle_timeout_in_seconds = 1200,
                    }
                }
              }
            }
          }
        }
        IPs = {
          public_ips = {
            PROD-IP-LB-1-KEY = {
              compartment_id     = null,
              lifetime           = "RESERVED"
              defined_tags       = null
              display_name       = "prod_ip_lb_1"
              freeform_tags      = null
              private_ip_id      = null
              public_ip_pool_id  = null
              public_ip_pool_key = null
            }
          }
        }
      }
      development = {
        category_freeform_tags = {
          "vision-sub-environment" = "dev"
        }
        non_vcn_specific_gateways = {
          l7_load_balancers = {
            EXAMPLE-02_LB_KEY = {
              compartment_id              = null,
              display_name                = "example-02"
              shape                       = "flexible"
              subnet_ids                  = null,
              subnet_keys                 = ["PRIVATE-APP-SUBNET-KEY"],
              defined_tags                = null,
              freeform_tags               = null,
              ip_mode                     = "IPV4",
              is_private                  = true,
              network_security_group_ids  = null,
              network_security_group_keys = ["NSG-APP-KEY"],
              reserved_ips_ids            = null,
              reserved_ips_keys           = null,
              shape_details = {
                maximum_bandwidth_in_mbps = 100,
                minimum_bandwidth_in_mbps = 10
              }

              backend_sets = {
                EXAMPLE-02-LB-BCK-END-SET-01 = {
                  health_checker = {
                    protocol            = "HTTP",
                    interval_ms         = 10000,
                    is_force_plain_text = true,
                    port                = 80,
                    retries             = 3,
                    return_code         = 200,
                    timeout_in_millis   = 3000,
                    url_path            = "/"
                  }
                  name   = "backend-set-01",
                  policy = "LEAST_CONNECTIONS",
                  session_persistence_configuration = {
                    cookie_name      = "example_cookie_2",
                    disable_fallback = false
                  }
                  backends = {
                    EXAMPLE-02-LB-BCK-END-SET-01-BE-01 = {
                      ip_address = "10.0.2.55",
                      port       = 80,
                    },
                    EXAMPLE-02-LB-BCK-END-SET-01-BE-02 = {
                      ip_address = "10.0.2.116",
                      port       = 80,
                    }
                  }
                }
              }
              cipher_suites = {
                EXAMPLE-02-LB-CIPHER-SUITE-01-KEY = {
                  name = "cipher_suite_01",
                  ciphers = [
                    "ECDHE-RSA-AES256-GCM-SHA384",
                    "ECDHE-ECDSA-AES256-GCM-SHA384",
                    "ECDHE-RSA-AES128-GCM-SHA256"
                  ]
                }
              }
              path_route_sets = {
                EXMPL_02_PATH_ROUTE_SET_01_KEY = {
                  name = "path_route_set_01",
                  path_routes = {
                    DEFAULT-KEY = {
                      backend_set_key = "EXAMPLE-02-LB-BCK-END-SET-01",
                      path            = "/"
                      path_match_type = {
                        match_type = "EXACT_MATCH"
                      }
                    }
                    CUSTOM-KEY = {
                      backend_set_key = "EXAMPLE-02-LB-BCK-END-SET-01",
                      path            = "/example/video/123"
                      path_match_type = {
                        match_type = "EXACT_MATCH"
                      }
                    }
                  }
                }
              }
              host_names = {
                LB2-HOSTNAME-1-KEY = {
                  hostname = "lb2test1.com",
                  name     = "lb2test1"
                }
                LB2-HOSTNAME-2-KEY = {
                  hostname = "lb2test2.com",
                  name     = "lb2test2"
                }
              }
              routing_policies = {
                LB2-ROUTE-POLICY-1-KEY = {
                  condition_language_version = "V1",
                  name                       = "example_routing_rules",
                  rules = {
                    HR-RULE-KEY = {
                      name      = "HR_mobile_user_rule"
                      condition = "all(http.request.headers[(i 'user-agent')] eq (i 'mobile'), http.request.url.query[(i 'department')] eq (i 'HR'))",
                      actions = {
                        ACTION-1-KEY = {
                          backend_set_key = "EXAMPLE-02-LB-BCK-END-SET-01",
                          name            = "FORWARD_TO_BACKENDSET",
                        }
                      }
                    }
                    DOCUMENTS-RULE-KEY = {
                      name      = "Documents_rule"
                      condition = "any(http.request.url.path eq (i '/documents'), http.request.headers[(i 'host')] eq (i 'doc.myapp.com'))",
                      actions = {
                        ACTION-1-KEY = {
                          backend_set_key = "EXAMPLE-02-LB-BCK-END-SET-01",
                          name            = "FORWARD_TO_BACKENDSET",
                        }
                      }
                    }
                  }
                }
              }
              rule_sets = {
                LB2-RULE-SET-1-KEY = {
                  name = "example_rule_set",
                  items = {
                    ITEM-1-KEY = {
                      action = "ADD_HTTP_REQUEST_HEADER",
                      header = "example_header_name",
                      value  = "example_value"
                    }
                    ITEM-2-KEY = {
                      action = "EXTEND_HTTP_REQUEST_HEADER_VALUE",
                      header = "example_header_name2",
                      value  = "example_value",
                      prefix : "example_prefix_value",
                      suffix : "example_suffix_value"
                    }
                    ITEM_3_KEY = {
                      action = "ADD_HTTP_RESPONSE_HEADER",
                      header = "example_header_name",
                      value  = "example_value"
                    }
                    ITEM_4_KEY = {
                      action      = "ALLOW",
                      description = "permitted internet clients",
                      conditions : {
                        CONDITION-1-KEY = {
                          attribute_name  = "SOURCE_IP_ADDRESS",
                          attribute_value = "192.168.0.0/16"
                        }
                      }
                    }
                    ITEM_5_KEY = {
                      action          = "CONTROL_ACCESS_USING_HTTP_METHODS",
                      allowed_methods = ["GET", "PUT", "POST", "PROPFIND"]
                    }
                    ITEM_6_KEY = {
                      action = "EXTEND_HTTP_REQUEST_HEADER_VALUE",
                      header = "example_header_name",
                      prefix = "example_prefix_value",
                      suffix = "example_suffix_value"
                    }
                    ITEM_7_KEY = {
                      action = "EXTEND_HTTP_RESPONSE_HEADER_VALUE",
                      header = "example_header_name",
                      prefix = "example_prefix_value",
                      suffix = "example_suffix_value"
                    }
                    ITEM_8_KEY = {
                      action = "HTTP_HEADER",
                      are_invalid_characters_allowed : false,
                      http_large_header_size_in_kb : 32
                    }
                    ITEM_9_KEY = {
                      action = "REDIRECT",
                      conditions = {
                        CONDITION-1-KEY = {
                          attribute_name  = "PATH",
                          attribute_value = "/example",
                          operator        = "SUFFIX_MATCH"
                        }
                      },
                      redirect_uri = {
                        protocol = "{protocol}",
                        host     = "in{host}",
                        port     = 8081,
                        path     = "{path}/video",
                        query    = "{query}"
                      },
                      response_code = 302
                    }
                    ITEM_10_KEY = {
                      action = "REMOVE_HTTP_REQUEST_HEADER",
                      header = "example_header_name"
                    }
                    ITEM_11_KEY = {
                      action = "REMOVE_HTTP_RESPONSE_HEADER",
                      header = "example_header_name"
                    }
                  }
                }
              }
              certificates = {
              }
              listeners = {
                LB2-LSNR-1-80 = {
                  default_backend_set_key = "EXAMPLE-02-LB-BCK-END-SET-01",
                  name                    = "lb2-lsnr1-80",
                  port                    = "80",
                  protocol                = "HTTP",
                  connection_configuration = {
                    idle_timeout_in_seconds = 1200,
                  }
                }

              }
            }
          }
        }
        IPs = {
          public_ips = {
            DEV-IP-LB-1-KEY = {
              compartment_id     = null,
              lifetime           = "RESERVED"
              defined_tags       = null
              display_name       = "dev_ip_lb_1"
              freeform_tags      = null
              private_ip_id      = null
              public_ip_pool_id  = null
              public_ip_pool_key = null
            }
          }
        }
      }
      oke-network = {
        vcns = {
          OKE-VCN = {
            display_name         = var.vcn_name
            cidr_blocks          = var.vcn_cidrs
            is_ipv6enabled       = false
            is_oracle_gua_enabled = false
            subnets = {
              oke-api-subnet = {
                display_name               = var.api_subnet_name
                cidr_block                 = var.api_subnet_cidr
                is_private                 = true
                prohibit_public_ip_on_vnic = true
                route_table_key            = "api-routetable"
                security_list_keys         = ["api-seclist"]
              }
              oke-workers-subnet = {
                display_name               = var.workers_subnet_name
                cidr_block                 = var.workers_subnet_cidr
                is_private                 = true
                prohibit_public_ip_on_vnic = true
                route_table_key            = "workers-routetable"
                security_list_keys         = ["workers-seclist"]
              }
              oke-services-subnet = {
                display_name               = var.services_subnet_name
                cidr_block                 = var.services_subnet_cidr
                is_private                 = false
                prohibit_public_ip_on_vnic = false
                route_table_key            = "services-routetable"
                security_list_keys         = ["services-seclist"]
              }
              oke-pods-subnet = {
                display_name               = var.pods_subnet_name
                cidr_block                 = var.pods_subnet_cidr
                is_private                 = true
                prohibit_public_ip_on_vnic = true
                route_table_key            = "pods-routetable"
                security_list_keys         = ["pods-seclist"]
              }
              oke-mgmt-subnet = {
                display_name               = var.mgmt_subnet_name
                cidr_block                 = var.mgmt_subnet_cidr
                is_private                 = true
                prohibit_public_ip_on_vnic = true
                route_table_key            = "mgmt-routetable"
                security_list_keys         = ["mgmt-seclist"]
              }
            }
            security_lists = {
              api-seclist = {
                display_name  = "API-Subnet-SecList"
                subnet_keys   = ["oke-api-subnet"]
                ingress_rules = [
                {
                  description = "Ingress ICMP for path discovery"
                  stateless   = false
                  protocol    = "ICMP"
                  src         = "0.0.0.0/0"
                  src_type    = "CIDR_BLOCK"
                  icmp_type   = 3
                  icmp_code   = 4
                }
              ]
              }
              workers-seclist = {
                display_name  = "Workers-Subnet-SecList"
                subnet_keys   = ["oke-workers-subnet"]
                ingress_rules = [
                {
                  description = "Ingress ICMP for path discovery"
                  stateless   = false
                  protocol    = "ICMP"
                  src         = "0.0.0.0/0"
                  src_type    = "CIDR_BLOCK"
                  icmp_type   = 3
                  icmp_code   = 4
                }
              ]
              }
              pods-seclist = {
                display_name  = "Pods-Subnet-SecList"
                subnet_keys   = ["oke-pods-subnet"]
                ingress_rules = [
                {
                  description = "Ingress ICMP for path discovery"
                  stateless   = false
                  protocol    = "ICMP"
                  src         = "0.0.0.0/0"
                  src_type    = "CIDR_BLOCK"
                  icmp_type   = 3
                  icmp_code   = 4
                }
              ]
              }
              services-seclist = {
                display_name  = "Services-Subnet-SecList"
                subnet_keys   = ["oke-services-subnet"]
                ingress_rules = [
                {
                  description = "Ingress ICMP for path discovery"
                  stateless   = false
                  protocol    = "ICMP"
                  src         = "0.0.0.0/0"
                  src_type    = "CIDR_BLOCK"
                  icmp_type   = 3
                  icmp_code   = 4
                }
              ]
              }
              mgmt-seclist = {
                display_name  = "Mgmt-Subnet-SecList"
                subnet_keys   = ["oke-mgmt-subnet"]
                egress_rules = [
                {
                  description  = "Allows outbound SSH traffic from mgmt subnet to hosts in the mgmt subnet, for Bastion service."
                  stateless    = false
                  protocol     = "TCP"
                  dst          = "10.0.3.0/28"
                  dst_type     = "CIDR_BLOCK"
                  dst_port_min = 22
                  dst_port_max = 22
                },
                {
                  description  = "Egress for bastion service to api endpoint"
                  stateless    = false
                  protocol     = "TCP"
                  dst          = "10.0.0.0/30"
                  dst_type     = "CIDR_BLOCK"
                  dst_port_min = 6443
                  dst_port_max = 6443
                },
                {
                  description  = "Egress for bastion service to worker nodes"
                  stateless    = false
                  protocol     = "TCP"
                  dst          = "10.0.1.0/24"
                  dst_type     = "CIDR_BLOCK"
                  dst_port_min = 22
                  dst_port_max = 22
                }
              ]
              ingress_rules = [
                {
                  description = "Ingress ICMP for path discovery"
                  stateless   = false
                  protocol    = "ICMP"
                  src         = "0.0.0.0/0"
                  src_type    = "CIDR_BLOCK"
                  icmp_type   = 3
                  icmp_code   = 4
                },
                {
                  description  = "Allows inbound SSH traffic from hosts in the mgmt subnet to the mgmt subnet, for Bastion service."
                  stateless    = false
                  protocol     = "TCP"
                  src          = "10.0.3.0/28"
                  src_type     = "CIDR_BLOCK"
                  dst_port_min = 22
                  dst_port_max = 22
                }
              ]
              }
            }
            route_tables = {
              api-routetable = {
                display_name = "API-Subnet-RouteTable"
                subnet_keys  = ["oke-api-subnet"]
                route_rules = {
                    sgw-route = {
                      destination      = "all-services",
                      destination_type = "SERVICE_CIDR_BLOCK",
                      network_entity_key     = "oke-sgw"
                    },
                    natgw-route = {
                      destination      = "0.0.0.0/0",
                      destination_type = "CIDR_BLOCK",
                      network_entity_key      = "oke-nat"
                    }
                }
              }
              workers-routetable = {
                display_name = "Workers-Subnet-RouteTable"
                subnet_keys  = ["oke-workers-subnet"]
                route_rules = {
                    sgw-route = {
                      destination      = "all-services",
                      destination_type = "SERVICE_CIDR_BLOCK",
                      network_entity_key    = "oke-sgw"
                    },
                    natgw-route = {
                      destination      = "0.0.0.0/0",
                      destination_type = "CIDR_BLOCK",
                      network_entity_key      = "oke-nat"
                    }
                }
              }
              pods-routetable = {
                display_name = "Pods-Subnet-RouteTable"
                subnet_keys  = ["oke-pods-subnet"]
                route_rules = {
                    sgw-route = {
                      destination      = "all-services",
                      destination_type = "SERVICE_CIDR_BLOCK",
                      network_entity_key     = "oke-sgw"
                    },
                    natgw-route = {
                      destination      = "0.0.0.0/0",
                      destination_type = "CIDR_BLOCK",
                      network_entity_key      = "oke-nat"
                    }
                }
              }
              services-routetable = {
                display_name = "Services-Subnet-RouteTable"
                subnet_keys  = ["oke-services-subnet"]
                route_rules = {
                    natgw-route = {
                      destination      = "0.0.0.0/0",
                      destination_type = "CIDR_BLOCK",
                      network_entity_key      = "oke-nat"
                    }
                }
              }
              mgmt-routetable = {
                display_name = "Mgmt-Subnet-RouteTable"
                subnet_keys  = ["oke-mgmt-subnet"]
                route_rules = {
                  sgw-route = {
                    network_entity_key = "oke-sgw"
                    destination        = "all-services"
                    destination_type   = "SERVICE_CIDR_BLOCK"
                  },
                  natgw-route = {
                    network_entity_key = "oke-nat"
                    destination        = "0.0.0.0/0"
                    destination_type   = "CIDR_BLOCK"
                  }
                }
              }
            }
            network_security_groups = {
              NSG-API = {
                display_name = "api-nsg"
                egress_rules = {
                  sgw-tcp = {
                    description = "Allow TCP egress from OKE control plane to OCI services"
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "all-services"
                    dst_type    = "SERVICE_CIDR_BLOCK"
                  }
                  workers-tcp-10250 = {
                    description  = "Allow TCP egress from OKE control plane to Kubelet on worker nodes."
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-WORKERS"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 10250
                    dst_port_max = 10250
                  }
                  workers-tcp-12250 = {
                    description  = "Allow TCP egress from OKE control plane to worker node"
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-WORKERS"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 12250
                    dst_port_max = 12250
                  }
                  api-tcp-6443 = {
                    description  = "Allow TCP egress for Kubernetes control plane inter-communicatioN"
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-API"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 6443
                    dst_port_max = 6443
                  }
                  workers-icmp = {
                    description = "Allow ICMP egress for path discovery to worker nodes"
                    stateless   = false
                    protocol    = "ICMP"
                    dst         = "NSG-WORKERS"
                    dst_type    = "NETWORK_SECURITY_GROUP"
                    icmp_type   = 3
                    icmp_code   = 4
                  }
                  #native
                  pods-all = {
                    description = "Allow Kubernetes API endpoint to communicate with pods."
                    stateless   = false
                    protocol    = "ALL"
                    dst         = "NSG-PODS"
                    dst_type    = "NETWORK_SECURITY_GROUP"
                  }
                }
                ingress_rules = {
                  api-tcp-6443 = {
                    description  = "Allow TCP ingress for Kubernetes control plane inter-communication."
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-API"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 6443
                    dst_port_max = 6443
                  }
                  operator-client-access = {
                    description  = "Operator access to Kubernetes API endpoint"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-MGMT"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 6443
                    dst_port_max = 6443
                  }
                  bastion-service-access = {
                    description  = "Bastion service access to Kubernetes API endpoint"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "10.0.3.0/28"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 6443
                    dst_port_max = 6443
                  }
                  workers-tcp-6443 = {
                    description  = "Allow TCP ingress to kube-apiserver from worker nodes"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-WORKERS"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 6443
                    dst_port_max = 6443
                  }
                  workers-tcp-10250 = {
                    description  = "Allow TCP ingress to OKE control plane from worker nodes"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-WORKERS"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 10250
                    dst_port_max = 10250
                  }
                  workers-tcp-12250 = {
                    description  = "Allow TCP ingress to OKE control plane from worker nodes"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-WORKERS"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 12250
                    dst_port_max = 12250
                  }
                  workers-icmp = {
                    description = "Allow ICMP ingress for path discovery from worker nodes."
                    stateless   = false
                    protocol    = "ICMP"
                    src         = "NSG-WORKERS"
                    src_type    = "NETWORK_SECURITY_GROUP"
                    icmp_type   = 3
                    icmp_code   = 4
                  }
                  #native
                  pods-tcp-6443 = {
                    description  = "Pod to Kubernetes API endpoint communication (when using VCN-native pod networking)"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-PODS"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 6443
                    dst_port_max = 6443
                  }
                  pods-tcp-12250 = {
                    description  = "Pod to Kubernetes API endpoint communication (when using VCN-native pod networking)"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-PODS"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 12250
                    dst_port_max = 12250
                  }
                }
              }
              NSG-WORKERS = {
                display_name = "workers-nsg"
                egress_rules = {
                  workers-all = {
                    description = "Allow ALL egress from workers to other workers."
                    stateless   = false
                    protocol    = "ALL"
                    dst         = "NSG-WORKERS"
                    dst_type    = "NETWORK_SECURITY_GROUP"
                  }
                  sgw-tcp = {
                    description = "Allow TCP egress from workers to OCI Services."
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "all-services"
                    dst_type    = "SERVICE_CIDR_BLOCK"
                  }
                  api-tcp-6443 = {
                    description  = "Allow TCP egress from workers to Kubernetes API server."
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-API"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 6443
                    dst_port_max = 6443
                  }
                  api-tcp-10250 = {
                    description  = "Allow TCP ingress to workers for health check from OKE control plane."
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-API"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 10250
                    dst_port_max = 10250
                  }
                  api-tcp-12250 = {
                    description  = "Allow TCP egress from workers to OKE control plane."
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-API"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 12250
                    dst_port_max = 12250
                  }
                  anywhere-icmp = {
                    description = "Path Discovery."
                    stateless   = false
                    protocol    = "ICMP"
                    dst         = "0.0.0.0/0"
                    dst_type    = "CIDR_BLOCK"
                    icmp_type   = 3
                    icmp_code   = 4
                  }
                  #native
                  pods-all = {
                    description = "Allow worker nodes to access pods."
                    stateless   = false
                    protocol    = "ALL"
                    dst         = "NSG-PODS"
                    dst_type    = "NETWORK_SECURITY_GROUP"
                  }
                  anywhere-tcp = {
                    description = "(optional) Allow worker nodes to communicate with internet."
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "0.0.0.0/0"
                    dst_type    = "CIDR_BLOCK"
                  }
                }

                ingress_rules = {
                  workers-all = {
                    description = "Allow ALL ingress to workers from other workers."
                    stateless   = false
                    protocol    = "ALL"
                    src         = "NSG-WORKERS"
                    src_type    = "NETWORK_SECURITY_GROUP"
                  }
                  api-all = {
                    description = "Allow ALL ingress to workers from Kubernetes control plane for webhooks served by workers."
                    stateless   = false
                    protocol    = "ALL"
                    src         = "NSG-API"
                    src_type    = "NETWORK_SECURITY_GROUP"
                  }
                  lb-tcp-10256 = {
                    description  = "Allow TCP ingress to workers for health check from public load balancers"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-SERVICES"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 10256
                    dst_port_max = 10256
                  }
                  lb-tcp = {
                    description  = "Allow TCP ingress to workers from public load balancers"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-SERVICES"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 30000
                    dst_port_max = 32767
                  }
                  anywhere-icmp = {
                    description = "Allow ICMP ingress to workers for path discovery."
                    stateless   = false
                    protocol    = "ICMP"
                    src         = "0.0.0.0/0"
                    src_type    = "CIDR_BLOCK"
                    icmp_type   = 3
                    icmp_code   = 4
                  }
                  operator-ssh-access = {
                    description  = "Operator ssh access to workers"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "NSG-MGMT"
                    src_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 22
                    dst_port_max = 22
                  }
                  bastion-service-access = {
                    description  = "Bastion service ssh access to workers"
                    stateless    = false
                    protocol     = "TCP"
                    src          = "10.0.3.0/28"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 22
                    dst_port_max = 22
                  }
                }
              }
              NSG-PODS = {
                display_name = "pods-nsg"
                egress_rules = {
                  pods-traffic = {
                    description = "Allow pods to communicate with other pods."
                    stateless   = false
                    protocol    = "ALL"
                    dst         = "NSG-PODS"
                    dst_type    = "NETWORK_SECURITY_GROUP"
                  }
                  sgw-icmp = {
                    description = "Path Discovery."
                    stateless   = false
                    protocol    = "ICMP"
                    dst         = "all-services"
                    dst_type    = "SERVICE_CIDR_BLOCK"
                    icmp_type   = 3
                    icmp_code   = 4
                  }
                  sgw-tcp = {
                    description = "Allow TCP egress from pods to OCI Services."
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "all-services"
                    dst_type    = "SERVICE_CIDR_BLOCK"
                  }
                  anywhere-tcp = {
                    description = "(optional) Allow pods nodes to communicate with internet."
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "0.0.0.0/0"
                    dst_type    = "CIDR_BLOCK"
                  }
                  api-tcp-6443 = {
                    description  = "Allow TCP egress from pods to Kubernetes API server."
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-API"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 6443
                    dst_port_max = 6443
                  }
                  api-tcp-12250 = {
                    description  = "Allow TCP egress from pods to OKE control plane."
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-API"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 12250
                    dst_port_max = 12250
                  }
                }
                ingress_rules = {
                  workers-all = {
                    description = "Allow worker nodes to access pods."
                    stateless   = false
                    protocol    = "ALL"
                    src         = "NSG-WORKERS"
                    src_type    = "NETWORK_SECURITY_GROUP"
                  }
                  api-all = {
                    description = "Allow Kubernetes API endpoint to communicate with pods."
                    stateless   = false
                    protocol    = "ALL"
                    src         = "NSG-API"
                    src_type    = "NETWORK_SECURITY_GROUP"
                  }
                  pods-all = {
                    description = "	Allow pods to communicate with other pods."
                    stateless   = false
                    protocol    = "ALL"
                    src         = "NSG-PODS"
                    src_type    = "NETWORK_SECURITY_GROUP"
                  }
                }
              }
              NSG-SERVICES = {
                display_name = "services-nsg"
                egress_rules = {

                  workers-tcp = {
                    description  = "Allow TCP egress from public load balancers to workers nodes for NodePort traffic"
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-WORKERS"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 30000
                    dst_port_max = 32767
                  }
                  workers-tcp-10256 = {
                    description  = "Allow TCP egress from public load balancers to worker nodes for health checks."
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-WORKERS"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 10256
                    dst_port_max = 10256
                  }
                  lb-workers-icmp = {
                    description = "Allow ICMP egress from public load balancers to worker nodes for path discovery."
                    stateless   = false
                    protocol    = "ICMP"
                    dst         = "NSG-WORKERS"
                    dst_type    = "NETWORK_SECURITY_GROUP"
                    icmp_type   = 3
                    icmp_code   = 4
                  }
                }
                ingress-rules = {
                  tcp_443 = {
                    description  = "Allow inbound traffic to Load Balancer."
                    stateless    = false
                    protocol     = "TCP"
                    src          = "0.0.0.0/0"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 443
                    dst_port_max = 443
                  }
                }
              }
              NSG-MGMT = {
                display_name = "mgmt-nsg"
                egress_rules = {
                  sgw-tcp = {
                    description = "Allows TCP outbound traffic from mgmt subnet to OCI Services Network (OSN)."
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "all-services"
                    dst_type    = "SERVICE_CIDR_BLOCK"
                  }
                  api-tcp = {
                    description  = "Allows TCP outbound traffic from mgmt subnet to Kubernetes API server, for OKE management."
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-API"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 6443
                    dst_port_max = 6443
                  }
                  workers-ssh-22 = {
                    description  = "Allows outbound SSH to worker nodes."
                    stateless    = false
                    protocol     = "TCP"
                    dst          = "NSG-WORKERS"
                    dst_type     = "NETWORK_SECURITY_GROUP"
                    dst_port_min = 22
                    dst_port_max = 22
                  }
                  anywhere-tcp = {
                    description = "Allows TCP egress from mgmt subnet to everywhere else."
                    stateless   = false
                    protocol    = "TCP"
                    dst         = "0.0.0.0/0"
                    dst_type    = "CIDR_BLOCK"
                  }
                }
                ingress_rules = {
                  access-ssh-22 = {
                    description  = "Allows inbound SSH access."
                    stateless    = false
                    protocol     = "TCP"
                    src          = "10.0.3.0/28"
                    src_type     = "CIDR_BLOCK"
                    dst_port_min = 22
                    dst_port_max = 22
                  }
                }
              }
            }
            vcn_specific_gateways = {
              internet_gateways = {
                oke-igw = {
                  display_name = "OKE-Internet-Gateway"
                  enabled   = true
                }
              }
              nat_gateways = {
                oke-nat = {
                  display_name = "OKE-NAT-Gateway"
                  block_traffic   = false
                }
              }
              service_gateways = {
                oke-sgw = {
                  display_name = "OKE-Service-Gateway"
                  services   = "all-services"
                }
              }
            }
          } # End of "OKE-VCN-1"
        } # End of "vcns"
      } # End of "oke-network"
    } # End of "network_configuration_categories"
  } # End of "network_configuration"
} # End of "locals"
