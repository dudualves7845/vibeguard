"""Unit tests for CodeAnalyzer."""

import unittest
from vibeguard.analyzer import CodeAnalyzer


class TestCodeAnalyzer(unittest.TestCase):

    def test_language_detection(self):
        self.assertEqual(CodeAnalyzer.detect_language("foo.py"), "python")
        self.assertEqual(CodeAnalyzer.detect_language("bar.ts"), "typescript")
        self.assertEqual(CodeAnalyzer.detect_language("baz.js"), "javascript")
        self.assertEqual(CodeAnalyzer.detect_language("other.txt"), "unknown")

    def test_python_symbol_and_stub_extraction(self):
        code = """
import os
import sys

def valid_function(a, b):
    \"\"\"Calculates sum.\"\"\"
    return a + b

def empty_stub():
    pass

class MyService:
    def process(self):
        return True
"""
        analysis = CodeAnalyzer.analyze_file("service.py", code)
        self.assertEqual(analysis.language, "python")
        self.assertIn("os", analysis.imports)
        self.assertIn("sys", analysis.imports)

        names = [s.name for s in analysis.symbols]
        self.assertIn("valid_function", names)
        self.assertIn("empty_stub", names)
        self.assertIn("MyService", names)

        # Check stub detected
        kinds = [p["kind"] for p in analysis.placeholders]
        self.assertIn("empty_stub", kinds)

    def test_js_ts_symbol_and_import_extraction(self):
        code = """
import express from 'express';
const path = require('path');

export function calculateTotal(items, tax) {
    return items.reduce((a, b) => a + b, 0) * (1 + tax);
}

export const fetchUsers = async (limit) => {
    return [];
};

export class OrderManager {}
"""
        analysis = CodeAnalyzer.analyze_file("orders.ts", code)
        self.assertEqual(analysis.language, "typescript")
        self.assertIn("express", analysis.imports)
        self.assertIn("path", analysis.imports)

        names = [s.name for s in analysis.symbols]
        self.assertIn("calculateTotal", names)
        self.assertIn("fetchUsers", names)
        self.assertIn("OrderManager", names)

    def test_secret_detection(self):
        stripe_key = "sk_" + "live_" + "123456789012345678901234"
        code = f"""
AWS_SECRET = "AKIA1234567890ABCDEF"
STRIPE_KEY = "{stripe_key}"
GENERIC = "api_key = 'abcdef1234567890deadbeef'"
"""
        analysis = CodeAnalyzer.analyze_file("config.py", code)
        self.assertGreaterEqual(len(analysis.secret_findings), 2)

    def test_scoped_python_symbols_and_class_stubs(self):
        code = """
class UserService:
    def execute(self, user_id):
        return True

    def _internal(self):
        pass

class EmptyStubClass:
    pass
"""
        analysis = CodeAnalyzer.analyze_file("service.py", code)
        names = {s.name: s for s in analysis.symbols}
        self.assertIn("UserService", names)
        self.assertIn("UserService.execute", names)
        self.assertEqual(names["UserService.execute"].kind, "method")
        self.assertTrue(names["UserService.execute"].is_exported)
        self.assertEqual(names["UserService.execute"].parameters, ["self", "user_id"])

        self.assertIn("UserService._internal", names)
        self.assertFalse(names["UserService._internal"].is_exported)

        # Check stub class detected
        kinds = [p["kind"] for p in analysis.placeholders]
        self.assertIn("empty_stub", kinds)
        self.assertTrue(any("Stub class 'EmptyStubClass'" in p["text"] for p in analysis.placeholders))

    def test_js_ts_multiline_imports_and_class_methods(self):
        code = """
import {
    QueryClient,
    useQuery
} from '@tanstack/react-query';

export class OrderService {
    constructor(db) {
        this.db = db;
    }

    public calculateDiscount(total: number): number {
        return total * 0.1;
    }

    private helper() {
        return false;
    }
}
"""
        analysis = CodeAnalyzer.analyze_file("order.ts", code)
        self.assertIn("@tanstack/react-query", analysis.imports)

        names = {s.name: s for s in analysis.symbols}
        self.assertIn("OrderService", names)
        self.assertIn("OrderService.calculateDiscount", names)
        self.assertEqual(names["OrderService.calculateDiscount"].kind, "method")
        self.assertTrue(names["OrderService.calculateDiscount"].is_exported)
        self.assertEqual(names["OrderService.calculateDiscount"].parameters, ["total"])

        self.assertIn("OrderService.helper", names)
        self.assertFalse(names["OrderService.helper"].is_exported)

    def test_jsx_tsx_tokens_and_react_components(self):
        code = """
import React, { useState } from 'react';
import type { UserProfile } from './types';

export interface ButtonProps {
    label: string;
    onClick: () => void;
}

export type ThemeMode = 'light' | 'dark';

export const ActionButton: React.FC<ButtonProps> = (props) => {
    return <button onClick={props.onClick}>{props.label}</button>;
};

export default function AppContainer({ title }: { title: string }) {
    return <div>{title}</div>;
}
"""
        analysis = CodeAnalyzer.analyze_file("components/App.tsx", code)
        self.assertEqual(analysis.language, "typescript")
        self.assertIn("react", analysis.imports)
        self.assertIn("./types", analysis.imports)

        names = {s.name: s for s in analysis.symbols}
        self.assertIn("ButtonProps", names)
        self.assertEqual(names["ButtonProps"].kind, "interface")
        self.assertTrue(names["ButtonProps"].is_exported)

        self.assertIn("ThemeMode", names)
        self.assertEqual(names["ThemeMode"].kind, "type")
        self.assertTrue(names["ThemeMode"].is_exported)

        self.assertIn("ActionButton", names)
        self.assertTrue(names["ActionButton"].is_exported)

        self.assertIn("AppContainer", names)
        self.assertTrue(names["AppContainer"].is_exported)

    def test_go_parsing(self):
        code = """
package service

import (
    "fmt"
    "net/http"
    "github.com/gorilla/mux"
)

type Server struct {
    port int
}

type Router interface {
    Handle(path string)
}

func (s *Server) Start(port int) error {
    return nil
}

func privateHelper(x int) bool {
    return true
}

func IncompleteTask() {
    panic("not implemented")
}
"""
        analysis = CodeAnalyzer.analyze_file("service.go", code)
        self.assertEqual(analysis.language, "go")
        self.assertIn("fmt", analysis.imports)
        self.assertIn("net/http", analysis.imports)
        self.assertIn("github.com/gorilla/mux", analysis.imports)

        names = {s.name: s for s in analysis.symbols}
        self.assertIn("Server", names)
        self.assertEqual(names["Server"].kind, "struct")
        self.assertTrue(names["Server"].is_exported)

        self.assertIn("Router", names)
        self.assertEqual(names["Router"].kind, "interface")
        self.assertTrue(names["Router"].is_exported)

        self.assertIn("Server.Start", names)
        self.assertEqual(names["Server.Start"].kind, "method")
        self.assertTrue(names["Server.Start"].is_exported)
        self.assertEqual(names["Server.Start"].parameters, ["port"])

        self.assertIn("privateHelper", names)
        self.assertFalse(names["privateHelper"].is_exported)

        # Check panic stub detected
        kinds = [p["kind"] for p in analysis.placeholders]
        self.assertIn("unimplemented_error", kinds)

    def test_rust_parsing(self):
        code = """
use std::collections::HashMap;
use serde::{Serialize, Deserialize};

pub struct UserRecord {
    pub id: u64,
}

pub trait Repository {
    fn find_by_id(&self, id: u64) -> Option<UserRecord>;
}

impl UserRecord {
    pub fn new(id: u64) -> Self {
        Self { id }
    }

    fn internal_calc(&self) -> u64 {
        self.id * 2
    }
}

pub fn process_data(batch: Vec<u8>) {
    todo!("will finish later")
}
"""
        analysis = CodeAnalyzer.analyze_file("model.rs", code)
        self.assertEqual(analysis.language, "rust")
        self.assertIn("std", analysis.imports)
        self.assertIn("serde", analysis.imports)

        names = {s.name: s for s in analysis.symbols}
        self.assertIn("UserRecord", names)
        self.assertEqual(names["UserRecord"].kind, "struct")
        self.assertTrue(names["UserRecord"].is_exported)

        self.assertIn("Repository", names)
        self.assertEqual(names["Repository"].kind, "trait")
        self.assertTrue(names["Repository"].is_exported)

        self.assertIn("UserRecord.new", names)
        self.assertTrue(names["UserRecord.new"].is_exported)

        self.assertIn("UserRecord.internal_calc", names)
        self.assertFalse(names["UserRecord.internal_calc"].is_exported)

        self.assertIn("process_data", names)
        self.assertTrue(names["process_data"].is_exported)

        # Check todo! stub detected
        kinds = [p["kind"] for p in analysis.placeholders]
        self.assertIn("unimplemented_error", kinds)

    def test_go_generics_and_receiver_extraction(self):
        code = """
package cache

type Cache[K comparable, V any] struct {
    items map[K]V
}

func (c *Cache[K, V]) Set(key K, val V) {
}

func ProcessItems[T any](items []T) int {
    return len(items)
}
"""
        analysis = CodeAnalyzer.analyze_file("cache.go", code)
        names = {s.name: s for s in analysis.symbols}
        self.assertIn("Cache", names)
        self.assertEqual(names["Cache"].kind, "struct")
        self.assertTrue(names["Cache"].is_exported)

        self.assertIn("Cache.Set", names)
        self.assertEqual(names["Cache.Set"].kind, "method")
        self.assertTrue(names["Cache.Set"].is_exported)

        self.assertIn("ProcessItems", names)
        self.assertEqual(names["ProcessItems"].kind, "function")
        self.assertTrue(names["ProcessItems"].is_exported)

    def test_rust_trait_impl_and_delimiters(self):
        code = """
use {
    serde::Serialize,
    tokio::sync::mpsc,
};

pub struct Worker;

pub trait Task {
    fn execute(&self);
}

impl Task for Worker {
    fn execute(&self) {
        todo!{}
    }
}

fn deferred() {
    unimplemented![]
}
"""
        analysis = CodeAnalyzer.analyze_file("worker.rs", code)
        self.assertIn("serde", analysis.imports)
        self.assertIn("tokio", analysis.imports)

        names = {s.name: s for s in analysis.symbols}
        self.assertIn("Worker.execute", names)
        self.assertEqual(names["Worker.execute"].kind, "method")

        # Check todo!{} and unimplemented![] both caught
        self.assertEqual(len(analysis.placeholders), 2)

    def test_typescript_generics_and_enums(self):
        code = """
export enum LogLevel {
    INFO,
    WARN,
    ERROR
}

export function identity<T>(value: T): T {
    return value;
}
"""
        analysis = CodeAnalyzer.analyze_file("logger.ts", code)
        names = {s.name: s for s in analysis.symbols}
        self.assertIn("LogLevel", names)
        self.assertEqual(names["LogLevel"].kind, "enum")
        self.assertTrue(names["LogLevel"].is_exported)

        self.assertIn("identity", names)
        self.assertEqual(names["identity"].kind, "function")
        self.assertTrue(names["identity"].is_exported)

    def test_js_comments_and_strings_stripped_for_imports_and_braces(self):
        code = """
        // import fake_pkg from 'fake-pkg';
        /* import { bad } from 'bad-pkg'; */
        /**
         * @see import('jsdoc-pkg')
         */
        const msg = "import inside_str from 'string-pkg'";
        const req = "require('string-req')";
        import realPkg from 'real-pkg';
        const realReq = require('real-req');

        export class ServiceWithBraces {
            public getTemplate() {
                const s = "}"; // brace inside string literal
                return `} ${s}`;
            }

            public getNext() {
                return 42;
            }
        }
        """
        analysis = CodeAnalyzer.analyze_file("service.ts", code)
        self.assertIn("real-pkg", analysis.imports)
        self.assertIn("real-req", analysis.imports)
        self.assertNotIn("fake-pkg", analysis.imports)
        self.assertNotIn("bad-pkg", analysis.imports)
        self.assertNotIn("jsdoc-pkg", analysis.imports)
        self.assertNotIn("string-pkg", analysis.imports)
        self.assertNotIn("string-req", analysis.imports)

        names = {s.name: s for s in analysis.symbols}
        self.assertIn("ServiceWithBraces.getTemplate", names)
        self.assertIn("ServiceWithBraces.getNext", names)

    def test_multiline_signatures_go_rust_js(self):
        # Go multiline
        go_code = """
        package api
        func (s *Server) Start(
            port int,
            host string,
        ) error {
            return nil
        }
        func CreateUser(
            name string,
            age int,
        ) bool {
            return true
        }
        """
        go_analysis = CodeAnalyzer.analyze_file("api.go", go_code)
        go_names = {s.name: s for s in go_analysis.symbols}
        self.assertIn("Server.Start", go_names)
        self.assertEqual(go_names["Server.Start"].parameters, ["port", "host"])
        self.assertIn("CreateUser", go_names)
        self.assertEqual(go_names["CreateUser"].parameters, ["name", "age"])

        # Rust multiline
        rust_code = """
        use tokio::{sync::mpsc};
        impl Server {
            pub fn handle_request(
                &self,
                req: Request,
                res: Response,
            ) -> Result<()> {
                Ok(())
            }
        }
        pub fn process_event(
            evt: Event,
            retry: u32,
        ) {
        }
        """
        rust_analysis = CodeAnalyzer.analyze_file("lib.rs", rust_code)
        self.assertIn("tokio", rust_analysis.imports)
        self.assertNotIn("sync", rust_analysis.imports)
        rust_names = {s.name: s for s in rust_analysis.symbols}
        self.assertIn("Server.handle_request", rust_names)
        self.assertEqual(rust_names["Server.handle_request"].parameters, ["&self", "req", "res"])
        self.assertIn("process_event", rust_names)
        self.assertEqual(rust_names["process_event"].parameters, ["evt", "retry"])

        # JS multiline
        js_code = """
        export function computeMetric(
            alpha: number,
            beta: number
        ): number {
            return alpha + beta;
        }
        export const fetchBatch = async (
            batchId: string,
            limit: number
        ) => {
            return [];
        };

        export class ServiceHelper {
            public computeTotal(
                val: number,
                taxRate: number
            ): number
            {
                return val * (1 + taxRate);
            }
        }
        """
        js_analysis = CodeAnalyzer.analyze_file("calc.ts", js_code)
        js_names = {s.name: s for s in js_analysis.symbols}
        self.assertIn("computeMetric", js_names)
        self.assertEqual(js_names["computeMetric"].parameters, ["alpha", "beta"])
        self.assertIn("fetchBatch", js_names)
        self.assertEqual(js_names["fetchBatch"].parameters, ["batchId", "limit"])
        self.assertIn("ServiceHelper.computeTotal", js_names)
        self.assertEqual(js_names["ServiceHelper.computeTotal"].parameters, ["val", "taxRate"])

    def test_python_full_parameters_and_nested_functions(self):
        code = """def outer_handler(pos, /, standard, *args, kw_only=1, **kwargs):
    def local_nested_helper(x):
        return x * 2
    return local_nested_helper(standard)

class HandlerClass:
    def method(self, arg1, *extra):
        class LocalInnerClass:
            def local_meth(self):
                pass
        def inner_sub():
            pass
        return arg1
"""
        analysis = CodeAnalyzer.analyze_file("handler.py", code)
        names = {s.name: s for s in analysis.symbols}
        self.assertIn("outer_handler", names)
        self.assertTrue(names["outer_handler"].is_exported)
        self.assertEqual(names["outer_handler"].parameters, ["pos", "standard", "*args", "kw_only", "**kwargs"])

        self.assertIn("outer_handler.local_nested_helper", names)
        self.assertFalse(names["outer_handler.local_nested_helper"].is_exported)
        self.assertEqual(names["outer_handler.local_nested_helper"].kind, "function")

        self.assertIn("HandlerClass.method", names)
        self.assertTrue(names["HandlerClass.method"].is_exported)
        self.assertEqual(names["HandlerClass.method"].parameters, ["self", "arg1", "*extra"])

        self.assertIn("HandlerClass.method.LocalInnerClass", names)
        self.assertFalse(names["HandlerClass.method.LocalInnerClass"].is_exported)
        self.assertEqual(names["HandlerClass.method.LocalInnerClass"].kind, "class")

        self.assertIn("HandlerClass.method.inner_sub", names)
        self.assertFalse(names["HandlerClass.method.inner_sub"].is_exported)

    def test_secret_detection_shannon_entropy(self):
        # Repeated character string should not trigger secret alert due to low entropy
        code = """
TEST_KEY = "api_key = 'aaaaaaaaaaaaaaaaaaaaaaaa'"
LOW_ENTROPY = "password = '12121212121212121212'"
REAL_KEY = "api_key = 'w9zKmP2vL8qR1xTy40Z1'"
"""
        analysis = CodeAnalyzer.analyze_file("config.py", code)
        matched_samples = [s["matched_sample"] for s in analysis.secret_findings]
        # Only REAL_KEY should be flagged
        self.assertEqual(len(analysis.secret_findings), 1)

    def test_java_parsing_symbols_and_stubs(self):
        code = """
package com.vibeguard.service;

import java.util.Map;
import java.io.IOException;

public class PaymentService {
    private final String apiKey;

    public PaymentService(String apiKey) {
        this.apiKey = apiKey;
    }

    public boolean charge(String customerId, double amount) {
        return true;
    }

    protected void rollback(String txId) {
        throw new UnsupportedOperationException("Not implemented yet");
    }
}
"""
        analysis = CodeAnalyzer.analyze_file("PaymentService.java", code)
        self.assertEqual(analysis.language, "java")
        self.assertIn("java.util.Map", analysis.imports)
        self.assertIn("java.io.IOException", analysis.imports)

        names = {s.name: s for s in analysis.symbols}
        self.assertIn("PaymentService", names)
        self.assertEqual(names["PaymentService"].kind, "class")
        self.assertTrue(names["PaymentService"].is_exported)

        self.assertIn("PaymentService.PaymentService", names)
        self.assertEqual(names["PaymentService.PaymentService"].parameters, ["apiKey"])
        self.assertTrue(names["PaymentService.PaymentService"].is_exported)

        self.assertIn("PaymentService.charge", names)
        self.assertEqual(names["PaymentService.charge"].parameters, ["customerId", "amount"])
        self.assertTrue(names["PaymentService.charge"].is_exported)

        self.assertIn("PaymentService.rollback", names)
        self.assertEqual(names["PaymentService.rollback"].parameters, ["txId"])

        # Check stub detected
        kinds = [p["kind"] for p in analysis.placeholders]
        self.assertIn("unimplemented_error", kinds)

        # Check package-private methods are recognized
        pkg_code = """
public class Account {
    void processTransaction(int id) {
        System.out.println(id);
    }
}
"""
        pkg_analysis = CodeAnalyzer.analyze_file("Account.java", pkg_code)
        pkg_names = {s.name: s for s in pkg_analysis.symbols}
        self.assertIn("Account.processTransaction", pkg_names)
        self.assertEqual(pkg_names["Account.processTransaction"].parameters, ["id"])
        self.assertFalse(pkg_names["Account.processTransaction"].is_exported)

    def test_dangerous_calls_detection(self):
        code = """
def run_dynamic(cmd):
    eval("1 + 1")
    exec("print('dangerous')")
    __import__("os")
    compile("code", "filename", "exec")
    return cmd
"""
        analysis = CodeAnalyzer.analyze_file("dynamic.py", code)
        self.assertEqual(len(analysis.dangerous_calls), 4)
        calls = [d["call"] for d in analysis.dangerous_calls]
        self.assertIn("eval", calls)
        self.assertIn("exec", calls)
        self.assertIn("__import__", calls)
        self.assertIn("compile", calls)

        # Test JS/TS dangerous calls
        js_code = """
const res = eval("2 + 2");
const fn = new Function("x", "return x * 2");
console.log("safe eval string: eval('test')");
"""
        js_analysis = CodeAnalyzer.analyze_file("dynamic.js", js_code)
        self.assertEqual(len(js_analysis.dangerous_calls), 2)
        js_calls = [d["call"] for d in js_analysis.dangerous_calls]
        self.assertIn("eval", js_calls)
        self.assertIn("new Function", js_calls)

    def test_cyclomatic_complexity_calculation(self):
        code = """
def complex_algorithm(x, y, z):
    if x > 0:
        for item in y:
            if item:
                z += 1
            elif item == 0:
                z -= 1
    elif x < 0:
        while z > 0:
            z -= 1
    return z
"""
        analysis = CodeAnalyzer.analyze_file("algo.py", code)
        fn_sym = next(s for s in analysis.symbols if s.name == "complex_algorithm")
        self.assertGreater(fn_sym.complexity, 5)

    def test_duplicate_function_declaration(self):
        code = """
def fetch_user(user_id):
    return {"id": user_id, "v": 1}

def fetch_user(user_id):
    return {"id": user_id, "v": 2}
"""
        analysis = CodeAnalyzer.analyze_file("service.py", code)
        dup_placeholders = [p for p in analysis.placeholders if p.get("kind") == "duplicate_declaration"]
        self.assertEqual(len(dup_placeholders), 1)
        self.assertIn("fetch_user", dup_placeholders[0]["text"])
        self.assertIn("overwrites", dup_placeholders[0]["text"])


if __name__ == "__main__":
    unittest.main()


