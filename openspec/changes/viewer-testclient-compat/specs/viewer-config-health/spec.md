## ADDED Requirements

### Requirement: HTTP测试客户端兼容依赖
viewer开发依赖与uv.lock MUST提供可复现的TestClient组合；干净frozen安装运行HTTP测试 MUST NOT出现Starlette的httpx弃用警告或AnyIO BlockingPortal弃用警告，MUST NOT通过过滤警告或修改第三方包实现。生产API/health语义 MUST保持。临时兼容上界 MUST说明原因及解除条件；共享lock运行依赖版本变化 MUST明确登记。

#### Scenario: 干净环境验证
- **WHEN** 使用支持的Python3.12从viewer执行uv sync --frozen并运行现有测试
- **THEN** 测试通过，两个指定弃用警告均不存在；现有health200/latestnull及输出不可枚举503/defaultdetail语义不变
