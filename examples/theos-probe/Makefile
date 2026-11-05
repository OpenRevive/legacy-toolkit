THEOS ?= $(HOME)/theos
THEOS_LEGACY_PREFIX ?= $(THEOS)/toolchain/legacy
TARGET := iphone:clang:6.1:6.0
ARCHS := armv7
DEBUG := 0
FINALPACKAGE := 1
THEOS_PACKAGE_SCHEME :=
THEOS_PLATFORM_DEB_COMPRESSION_TYPE := gzip

TARGET_STRIP := $(THEOS_LEGACY_PREFIX)/bin/arm-apple-darwin11-strip
TARGET_CODESIGN_ALLOCATE := $(THEOS_LEGACY_PREFIX)/bin/arm-apple-darwin11-codesign_allocate
TARGET_CODESIGN_FLAGS := -S -Hsha1

include $(THEOS)/makefiles/common.mk

APPLICATION_NAME := ORTheosProbe
ORTheosProbe_FILES := main.m
ORTheosProbe_FRAMEWORKS := UIKit Foundation CoreGraphics
ORTheosProbe_USE_MODULES := 0
ORTheosProbe_CFLAGS := -fno-objc-arc
# Use Clang's legacy linker-argument mode; the actual ld64 version is 956.6.
ORTheosProbe_LDFLAGS := -fuse-ld=$(THEOS_LEGACY_PREFIX)/bin/arm-apple-darwin11-ld -mlinker-version=450 -Wl,-no_data_const -Wl,-no_fixup_chains -Wl,-sdk_version,6.1

TOOL_NAME := or-theos-check
or-theos-check_FILES := Tool.m
or-theos-check_FRAMEWORKS := Foundation UIKit CoreGraphics
or-theos-check_USE_MODULES := 0
or-theos-check_CFLAGS := -fno-objc-arc
or-theos-check_LDFLAGS := -fuse-ld=$(THEOS_LEGACY_PREFIX)/bin/arm-apple-darwin11-ld -mlinker-version=450 -Wl,-no_data_const -Wl,-no_fixup_chains -Wl,-sdk_version,6.1

include $(THEOS_MAKE_PATH)/application.mk
include $(THEOS_MAKE_PATH)/tool.mk
